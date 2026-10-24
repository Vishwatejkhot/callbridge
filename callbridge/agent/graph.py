"""The conversation brain: one LangGraph run per caller turn.

    screen -> understand -> verify -> find_options -> confirm -> act
                         +-> faq | take_message | handoff | goodbye | clarify

`screen` runs before any model sees the words (red flags, manipulation attempts). The LLM is only
used inside `understand`, to turn speech into a structured NLUResult. Every reply is written by code
from verified data, and every change goes through tools.booking.propose_and_execute, which runs
the safety checker. So even a fully hijacked model can at worst misunderstand: it cannot book,
cancel, or read out anything the verified caller isn't entitled to."""

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.agent.llm import LLMUnavailable, NLUEngine
from callbridge.agent.nlu import NLUContext, NLUResult, normalise
from callbridge.agent.prompts import PromptConfig
from callbridge.db.models import Booking, Clinic, Message
from callbridge.knowledge import store as knowledge
from callbridge.memory import store as memory
from callbridge.observability import HANDOFFS, REDFLAGS, SAFETY_BLOCKS
from callbridge.safety import injection, redflags
from callbridge.safety.proposals import (
    BookProposal, CallerContext, CancelProposal, ForgetProposal, RescheduleProposal,
)
from callbridge.tools import availability, booking, identity
from callbridge.tools.booking import Slot, speak_date, speak_datetime, speak_on, speak_time

TASKS = ("book", "reschedule", "cancel", "forget")
TASK_STAGES = ("identity", "which_booking", "preference", "slot_choice", "confirm")


class CallState(TypedDict, total=False):
    call_id: str
    clinic_id: int
    clinic_name: str
    caller_phone: str | None
    user_text: str
    nlu: dict
    nlu_meta: dict
    reply: str
    prefix: str
    last_question: str
    awaiting: str | None  # task | identity | which_booking | preference | slot_choice | confirm | anything_else | message_body | offer_message
    task: str | None
    identity: dict
    recognised_patient_id: int | None
    verified_patient_id: int | None
    verified_name: str | None
    patient_created: bool
    verify_attempts: int
    memory: dict
    target_booking_id: int | None
    target_desc: str | None
    target_practitioner_id: int | None
    prefs: dict
    asked_pref: bool
    offered: list
    chosen: dict | None
    retried: bool
    status: str  # active | escalated | handoff | ended
    flags: list
    events: list
    confidence: float
    unknown_count: int
    turns: int
    path: list
    next: str
    transfer: bool
    llm_down: bool
    pending: dict | None  # entities from the utterance that started the task
    repeats: int


@dataclass
class Deps:
    s: Session
    engine: NLUEngine
    config: PromptConfig
    practitioners: list[dict] = field(default_factory=list)  # {id, name, first, service}
    turn_meta: dict = field(default_factory=dict)
    prefetched: tuple | None = None  # (NLUResult, TurnMeta) drafted while the caller was still talking


def nlu_context(st: CallState, config: PromptConfig, practitioners: list[dict]) -> NLUContext:
    return NLUContext(
        today=clock.now().date(), awaiting=st.get("awaiting"), practitioner_names=[p["first"] for p in practitioners],
        config=config, last_bot_utterance=st.get("reply", ""), offered_count=len(st.get("offered", [])),
    )


def new_state(call_id: str, clinic: Clinic, caller_phone: str | None, recognised_patient_id: int | None) -> CallState:
    return CallState(
        call_id=call_id, clinic_id=clinic.id, clinic_name=clinic.name, caller_phone=caller_phone, user_text="",
        nlu={}, nlu_meta={}, reply="", prefix="", last_question="How can I help?", awaiting="task", task=None,
        identity={}, recognised_patient_id=recognised_patient_id, verified_patient_id=None, verified_name=None,
        patient_created=False, verify_attempts=0, memory={}, target_booking_id=None, target_desc=None,
        target_practitioner_id=None, prefs={}, asked_pref=False, offered=[], chosen=None, retried=False,
        status="active", flags=[], events=[], confidence=1.0, unknown_count=0, turns=0, path=[], next="",
        transfer=False, llm_down=False, pending=None, repeats=0,
    )


# ---------------------------------------------------------------- helpers


def _say(st: CallState, text: str) -> None:
    st["reply"] = (st.get("prefix", "") + " " + text).strip()
    st["prefix"] = ""


def _ask(st: CallState, text: str, awaiting: str) -> None:
    if text == st.get("last_question") and awaiting == st.get("awaiting") and awaiting != "message_body":
        st["repeats"] = st.get("repeats", 0) + 1
        if st["repeats"] >= 3:  # asked the same thing three times: we're stuck, stop looping
            st["repeats"] = 0
            _give_up(st)
            return
    else:
        st["repeats"] = 0
    _say(st, text)
    st["last_question"] = text
    st["awaiting"] = awaiting
    st["next"] = END


def _event(st: CallState, name: str) -> None:
    st["events"] = [*st["events"], name]


def _flag(st: CallState, name: str) -> None:
    if name not in st["flags"]:
        st["flags"] = [*st["flags"], name]


def _nlu(st: CallState) -> NLUResult:
    return NLUResult.model_validate(st["nlu"]) if st.get("nlu") else NLUResult()


def _ctx(st: CallState) -> CallerContext:
    return CallerContext(call_id=st["call_id"], clinic_id=st["clinic_id"], caller_phone=st.get("caller_phone"),
                         verified_patient_id=st.get("verified_patient_id"),
                         patient_created_this_call=st.get("patient_created", False))


def _reset_task(st: CallState) -> None:
    st.update(task=None, target_booking_id=None, target_desc=None, target_practitioner_id=None, prefs={},
              asked_pref=False, offered=[], chosen=None, retried=False, pending=None)


def _anything_else(st: CallState, lead: str) -> None:
    _ask(st, f"{lead} Is there anything else I can help with?".strip(), "anything_else")


def _first(name: str) -> str:
    return name.split()[0]


def _node(fn):
    """Run a node on a private copy of the state and return only what changed."""

    def wrapper(state: CallState, config) -> dict[str, Any]:
        deps: Deps = config["configurable"]["deps"]
        st = copy.deepcopy(state)
        st["path"] = [*st.get("path", []), fn.__name__]
        fn(st, deps)
        return {k: v for k, v in st.items() if state.get(k) != v}

    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------- nodes


@_node
def screen(st: CallState, d: Deps) -> None:
    text = st["user_text"]
    norm = normalise(text)  # "my chest, um, feels like, tight" must still trigger
    st["turns"] = st.get("turns", 0) + 1
    st["next"] = "understand"
    category = redflags.detect(text) or redflags.detect(norm)
    if category:
        REDFLAGS.labels(category).inc()
        HANDOFFS.labels("redflag").inc()
        _event(st, f"redflag:{category}")
        _flag(st, "redflag")
        st["status"] = "escalated"
        d.s.add(Message(clinic_id=st["clinic_id"], call_id=st["call_id"], caller_phone=st.get("caller_phone"),
                        caller_name=st.get("verified_name"), urgent=True,
                        body=f"URGENT: caller mentioned a possible red-flag symptom ({category.replace('_', ' ')}). "
                             "They were told to call 999 if severe, otherwise NHS 111. Please call back."))
        _ask(st, redflags.reply_for(category) + " Is there anything else I can help with?", "anything_else")
        _reset_task(st)
        return
    if st.get("awaiting") == "message_body":
        return  # a message for staff is passed on verbatim; it's never acted on
    kind = injection.classify(text) or injection.classify(norm)
    if kind:
        _event(st, f"refused:{kind}")
        if kind != "medical_advice":
            _flag(st, f"suspicious:{kind}")
        resume = f" {st['last_question']}" if st.get("awaiting") in TASK_STAGES else ""
        _say(st, injection.REPLIES[kind].split(". ")[0] + "." + resume if resume else injection.REPLIES[kind])
        st["next"] = END


@_node
def understand(st: CallState, d: Deps) -> None:
    try:
        if d.prefetched is not None:
            r, meta = d.prefetched
        else:
            r, meta = d.engine.parse(st["user_text"], nlu_context(st, d.config, d.practitioners))
    except LLMUnavailable:
        st["llm_down"] = True
        _event(st, "llm_down")
        st["next"] = "take_message"
        return
    d.turn_meta.update(meta.__dict__)
    st["nlu"] = r.model_dump(mode="json")
    st["confidence"] = round(0.7 * st.get("confidence", 1.0) + 0.3 * r.confidence, 3)

    i, a, task = r.intent, st.get("awaiting"), st.get("task")
    if a == "message_body":
        st["next"] = "take_message"
    elif i == "human":
        st["next"] = "handoff"
    elif i == "message":
        st["next"] = "take_message"
    elif i in TASKS and i != task:
        if task:
            _event(st, f"changed_mind:{task}->{i}")
        _reset_task(st)
        st["task"] = i
        st["pending"] = st["nlu"]
        st["next"] = "verify"
    elif i == "question" and not (a == "identity" and (r.dob or r.first_name)) and not (
            task and any([r.day, r.time, r.daypart, r.practitioner, r.choice])):  # "could I do Friday instead?"
        st["next"] = "faq"
    elif a == "offer_message" and i in ("affirm", "deny", "thanks", "goodbye"):
        if i == "affirm":
            st["next"] = "take_message"
        else:
            _anything_else(st, "No problem.")
    elif a in ("anything_else", "task") and i in ("deny", "goodbye", "thanks") and not task:
        st["next"] = "goodbye"
    elif task:
        st["next"] = "verify"
    elif any([r.day, r.time, r.daypart, r.practitioner, r.service]) and i in ("give_info", "unknown", "affirm"):
        st["task"] = "book"  # "Thursday afternoon with Tom?" is a booking request
        st["pending"] = st["nlu"]
        st["next"] = "verify"
    elif i == "goodbye":
        st["next"] = "goodbye"
    else:
        st["next"] = "clarify"


@_node
def verify(st: CallState, d: Deps) -> None:
    if st.get("verified_patient_id"):
        st["next"] = _after_verify(st)
        return
    r = _nlu(st)
    ident = dict(st.get("identity", {}))
    for k in ("first_name", "last_name"):
        if getattr(r, k):
            ident[k] = getattr(r, k)
    if r.dob:
        ident["dob"] = r.dob.isoformat()
    st["identity"] = ident
    if st.get("awaiting") == "identity" and not (r.first_name or r.last_name or r.dob):
        st["unknown_count"] = st.get("unknown_count", 0) + 1
        _event(st, "not_understood")
        if st["unknown_count"] >= 3:
            _give_up(st)
            return
    first, last = ident.get("first_name"), ident.get("last_name")
    recognised = st.get("recognised_patient_id")

    if "dob" not in ident:
        if recognised and not first:
            _ask(st, "Can I just confirm your date of birth?", "identity")
        elif first and last:
            _ask(st, f"Thanks{', ' + first if first else ''}. And your date of birth?", "identity")
        elif first:
            _ask(st, "Thanks. Could I take your surname and date of birth?", "identity")
        else:
            _ask(st, "Of course. Can I take your full name and date of birth, please?", "identity")
        return

    dob = datetime.fromisoformat(ident["dob"]).date()
    patient = identity.verify(d.s, st["clinic_id"], dob=dob, phone=st.get("caller_phone") if recognised else None,
                              first_name=first, last_name=last)
    if patient is None and not (first and last) and not recognised:
        _ask(st, "Thanks. And could I take your full name?", "identity")
        return
    if patient is None and st["task"] == "book" and first and last and not identity.name_exists(d.s, st["clinic_id"], first, last):
        patient = identity.register(d.s, st["clinic_id"], first_name=first, last_name=last, dob=dob, phone=st.get("caller_phone"))
        st["patient_created"] = True
        _event(st, "new_patient")
        st["prefix"] = f"Thanks, {patient.first_name}, I've set you up as a new patient."
    if patient is None:
        st["verify_attempts"] = st.get("verify_attempts", 0) + 1
        st["identity"] = {}
        _event(st, "verify_failed")
        if st["verify_attempts"] >= 2:
            _flag(st, "verify_failed")
            _reset_task(st)
            _ask(st, "I'm sorry, I can't match those details to our records, so I can't make changes over the phone. "
                     "I can take a message and the team will call you back. What would you like me to pass on?", "message_body")
        else:
            _ask(st, "Sorry, I couldn't match those details. Could you give me your full name and date of birth again?", "identity")
        return

    st["verified_patient_id"] = patient.id
    st["verified_name"] = patient.full_name
    _event(st, "verified")
    mem = memory.recall(d.s, st["clinic_id"], patient.id)
    if mem:
        st["memory"] = {"usual_practitioner_id": mem.usual_practitioner_id, "usual_practitioner_name": mem.usual_practitioner_name,
                        "last_visit": mem.last_visit.isoformat() if mem.last_visit else None}
    if not st.get("prefix"):
        st["prefix"] = f"Thanks, {patient.first_name}."
    st["next"] = _after_verify(st)


def _after_verify(st: CallState) -> str:
    if st.get("awaiting") == "confirm" and (st.get("chosen") or st["task"] in ("cancel", "forget")):
        return "confirm"
    return "confirm" if st["task"] == "forget" else "find_options"


@_node
def find_options(st: CallState, d: Deps) -> None:
    r = _nlu(st)
    task = st["task"]
    pending = NLUResult.model_validate(st["pending"]) if st.get("pending") else None
    st["pending"] = None

    if task in ("reschedule", "cancel") and not st.get("target_booking_id"):
        if not _select_booking(st, d, r if (r.day or r.time or r.choice) else (pending or r)):
            return
        if task == "cancel":
            st["next"] = "confirm"
            return
        r, pending = NLUResult(intent=r.intent), None  # those entities named the old booking, not the new time

    prefs = dict(st.get("prefs", {}))
    if pending is not None:
        _merge_prefs(prefs, pending, d)  # "a sports massage with Jake on Friday" said before we verified them
    changed = _merge_prefs(prefs, r, d)
    st["prefs"] = prefs

    if st.get("awaiting") == "slot_choice" and st.get("offered"):
        pick = _choose(st["offered"], r, d)
        if pick:
            st["chosen"] = pick
            st["next"] = "confirm"
            return
        if r.intent == "deny" and not changed:
            st["offered"] = []
            st["prefs"] = {k: v for k, v in prefs.items() if k in ("practitioner_id", "service")}
            _ask(st, "No problem. What other day or time would suit you?", "preference")
            return
        if not changed:
            _ask(st, f"Sorry, which would you prefer: {_options_text(st['offered'])}?", "slot_choice")
            return

    has_when = any(prefs.get(k) for k in ("day", "time", "daypart", "any_time"))
    if not has_when and not st.get("asked_pref"):
        st["asked_pref"] = True
        usual = st.get("memory", {}).get("usual_practitioner_name")
        lead = f"Would you like to see {_first(usual)} again, like last time? And what" if (
            task == "book" and usual and not prefs.get("practitioner_id")) else "What"
        _ask(st, f"{lead} day and time would suit you?", "preference")
        return

    slots, note = _search(st, d, prefs)
    if not slots:
        _ask(st, f"{note}I'm sorry, I can't find any free appointments for that. Would you like me to take a message "
                 "so the team can find you a time?", "offer_message")
        return
    if prefs.get("time") and prefs.get("day") and slots[0].start.strftime("%H:%M") == prefs["time"] and \
            slots[0].start.date().isoformat() == prefs["day"]:
        st["chosen"] = slots[0].to_dict()
        st["next"] = "confirm"
        return
    st["offered"] = [sl.to_dict() for sl in slots]
    _event(st, "offered_slots")
    question = "Which works better?" if len(slots) > 1 else "Would that work?"
    _ask(st, f"{note}{_offer_text(slots)} {question}", "slot_choice")


def _select_booking(st: CallState, d: Deps, r: NLUResult) -> bool:
    upcoming = booking.upcoming_bookings(d.s, st["clinic_id"], st["verified_patient_id"])
    if not upcoming:
        _reset_task(st)
        _ask(st, "I can't see any upcoming appointments for you. Would you like to book one?", "task")
        st["task"] = None
        return False
    pick: Booking | None = None
    if len(upcoming) == 1:
        pick = upcoming[0]
    else:
        if r.day:
            same_day = [b for b in upcoming if b.start.date() == r.day]
            pick = same_day[0] if len(same_day) == 1 else None
        if pick is None and r.time:
            same_time = [b for b in upcoming if b.start.strftime("%H:%M") == r.time]
            pick = same_time[0] if len(same_time) == 1 else None
        if pick is None and r.choice and st.get("awaiting") == "which_booking":
            pick = upcoming[r.choice - 1] if 0 < r.choice <= len(upcoming) else upcoming[-1]
        if pick is None:
            listed = " or ".join(f"the one {speak_on(b.start)}" for b in upcoming[:3])
            _ask(st, f"You have a few appointments coming up. Is it {listed}?", "which_booking")
            return False
    st["target_booking_id"] = pick.id
    st["target_practitioner_id"] = pick.practitioner_id
    st["target_desc"] = f"{speak_on(pick.start)} with {_first(pick.practitioner.name)}"
    return True


def _merge_prefs(prefs: dict, r: NLUResult, d: Deps) -> bool:
    before = dict(prefs)
    if r.day:
        prefs["day"] = r.day.isoformat()
        prefs.pop("any_time", None)
    if r.time:
        prefs["time"] = r.time
        prefs.pop("daypart", None)
    if r.daypart:
        prefs["daypart"] = r.daypart
        prefs.pop("time", None)
    if r.any_time:
        prefs["any_time"] = True
    if r.practitioner:
        match = next((p for p in d.practitioners if p["first"].lower() == r.practitioner.lower()), None)
        if match:
            prefs["practitioner_id"] = match["id"]
    if r.service:
        prefs["service"] = r.service
    return prefs != before


def _search(st: CallState, d: Deps, prefs: dict) -> tuple[list[Slot], str]:
    clinic_id = st["clinic_id"]
    limit = d.config.max_slots_offered
    ignore = st.get("target_booking_id")
    day = datetime.fromisoformat(prefs["day"]).date() if prefs.get("day") else None
    explicit_prac = prefs.get("practitioner_id")
    implicit_prac = None
    if not explicit_prac:
        implicit_prac = st.get("target_practitioner_id") if st["task"] == "reschedule" else \
            st.get("memory", {}).get("usual_practitioner_id")
        if implicit_prac and prefs.get("service"):
            svc = next((p["service"] for p in d.practitioners if p["id"] == implicit_prac), None)
            implicit_prac = implicit_prac if svc == prefs["service"] else None
    service = None if explicit_prac else prefs.get("service")

    def run(prac, on_day, lim=limit):
        slots = booking.check_slots(d.s, clinic_id, day=on_day, practitioner_id=prac, service=service,
                                    daypart=prefs.get("daypart"), limit=1000, ignore_booking_id=ignore, min_distinct=lim)
        if prefs.get("time") and slots:
            want = int(prefs["time"][:2]) * 60 + int(prefs["time"][3:])
            exact = [sl for sl in slots if sl.start.hour * 60 + sl.start.minute == want]
            if exact and on_day:
                return exact[:1]
            slots = sorted(slots, key=lambda sl: (abs(sl.start.hour * 60 + sl.start.minute - want), sl.start))
            slots = sorted(slots[:lim], key=lambda sl: sl.start)
        return booking._spread(slots, lim)

    for prac in ([explicit_prac] if explicit_prac else [implicit_prac, None] if implicit_prac else [None]):
        if day:
            slots = run(prac, day)
            if slots:
                return slots, ""
        else:
            slots = run(prac, None)
            if slots:
                return slots, ""
    if day:  # nothing that day: look at the following fortnight
        for i in range(1, 15):
            for prac in ([explicit_prac] if explicit_prac else [implicit_prac, None] if implicit_prac else [None]):
                slots = run(prac, day + timedelta(days=i))
                if slots:
                    return slots, f"Sorry, there's nothing free on {speak_date(day)}. "
    return [], ""


def _choose(offered: list[dict], r: NLUResult, d: Deps) -> dict | None:
    slots = [Slot.from_dict(o) for o in offered]
    if r.time:
        matches = [o for o, sl in zip(offered, slots, strict=False) if sl.start.strftime("%H:%M") == r.time
                   and (not r.day or sl.start.date() == r.day)]
        if len(matches) == 1:
            return matches[0]
    if r.practitioner:
        matches = [o for o in offered if o["practitioner_name"].split()[0].lower() == r.practitioner.lower()]
        if len(matches) == 1:
            return matches[0]
    if r.day and not r.time:
        matches = [o for o, sl in zip(offered, slots, strict=False) if sl.start.date() == r.day]
        if len(matches) == 1:
            return matches[0]
    if r.choice:
        idx = len(offered) - 1 if r.choice == -1 else r.choice - 1
        if 0 <= idx < len(offered):
            return offered[idx]
    if r.intent == "affirm" and len(offered) == 1:
        return offered[0]
    return None


def _offer_text(slots: list[Slot]) -> str:
    names = {sl.practitioner_name for sl in slots}
    days = {sl.start.date() for sl in slots}
    if len(names) == 1 and len(days) == 1:
        times = " or ".join(speak_time(sl.start) for sl in slots)
        return f"{_first(slots[0].practitioner_name)} has {speak_date(slots[0].start.date())} at {times}."
    if len(names) == 1:
        return f"{_first(slots[0].practitioner_name)} has " + " or ".join(speak_datetime(sl.start) for sl in slots) + "."
    parts = [f"{speak_datetime(sl.start)} with {_first(sl.practitioner_name)}" for sl in slots]
    return "I have " + " or ".join(parts) + "."


def _options_text(offered: list[dict]) -> str:
    return " or ".join(f"{speak_datetime(Slot.from_dict(o).start)} with {_first(o['practitioner_name'])}" for o in offered)


@_node
def confirm(st: CallState, d: Deps) -> None:
    r = _nlu(st)
    task = st["task"]
    if st.get("awaiting") == "confirm":
        if r.intent == "affirm" or (r.intent == "give_info" and r.confidence >= 0.9 and not any([r.day, r.time, r.choice])):
            st["next"] = "act"
            return
        new_when = any([r.day, r.time, r.daypart, r.practitioner])
        if r.intent == "deny" or new_when:
            if task in ("cancel", "forget"):
                _reset_task(st)
                _anything_else(st, "Okay, I'll leave it as it is.")
                return
            st["chosen"], st["offered"] = None, []
            if new_when:
                st["prefs"] = {k: v for k, v in st.get("prefs", {}).items() if k in ("practitioner_id", "service")}
                st["awaiting"] = "preference"
                st["next"] = "find_options"
                return
            st["prefs"] = {k: v for k, v in st.get("prefs", {}).items() if k in ("practitioner_id", "service")}
            _ask(st, "No problem. What day or time would suit you instead?", "preference")
            return
        _ask(st, "Sorry, I didn't catch that. " + st["last_question"].removeprefix("Sorry, I didn't catch that. "), "confirm")
        return

    if task == "book":
        sl = Slot.from_dict(st["chosen"])
        svc = next((p["service"] for p in d.practitioners if p["id"] == sl.practitioner_id), "")
        text = f"Just to confirm, that's {_article(svc)} {svc} appointment with {_first(sl.practitioner_name)} {speak_on(sl.start)}. Shall I book that?"
    elif task == "reschedule":
        sl = Slot.from_dict(st["chosen"])
        text = (f"So that's moving your appointment {st['target_desc']} to {speak_datetime(sl.start)} "
                f"with {_first(sl.practitioner_name)}. Shall I go ahead?")
    elif task == "cancel":
        text = f"Just to confirm, you'd like to cancel your appointment {st['target_desc']}. Is that right?"
    else:
        text = ("Just to confirm, you'd like us to delete your details and appointment history from our system. "
                "This can't be undone. Shall I go ahead?")
    _ask(st, text, "confirm")


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


@_node
def act(st: CallState, d: Deps) -> None:
    task, ctx = st["task"], _ctx(st)
    chosen = Slot.from_dict(st["chosen"]) if st.get("chosen") else None
    if task == "book":
        proposal = BookProposal(patient_id=st["verified_patient_id"], practitioner_id=chosen.practitioner_id, start=chosen.start)
    elif task == "reschedule":
        proposal = RescheduleProposal(booking_id=st["target_booking_id"], practitioner_id=chosen.practitioner_id, start=chosen.start)
    elif task == "cancel":
        proposal = CancelProposal(booking_id=st["target_booking_id"])
    else:
        proposal = ForgetProposal(patient_id=st["verified_patient_id"])

    result = booking.propose_and_execute(d.s, ctx, proposal)
    if result.ok:
        texted = " I've sent you a text to confirm." if result.sms_sent and st.get("caller_phone") else ""
        lead = {
            "book": f"Done. You're booked with {_first(chosen.practitioner_name) if chosen else ''} {speak_on(chosen.start) if chosen else ''}.{texted}",
            "reschedule": f"Done. You're now booked with {_first(chosen.practitioner_name) if chosen else ''} {speak_on(chosen.start) if chosen else ''}.{texted}",
            "cancel": f"Done, that appointment is cancelled.{texted}",
            "forget": "Done. I've deleted your details and appointment history from our system.",
        }[task]
        _event(st, {"book": "booked", "reschedule": "rescheduled", "cancel": "cancelled", "forget": "forgotten"}[task])
        if task == "forget":
            st.update(verified_patient_id=None, verified_name=None, recognised_patient_id=None, memory={}, identity={})
        _reset_task(st)
        _anything_else(st, lead)
        return

    for reason in result.decision.reasons:
        SAFETY_BLOCKS.labels(reason).inc()
    _event(st, "blocked:" + ",".join(result.decision.reasons))
    if result.decision.reasons == ["slot_taken"] and not st.get("retried"):
        st.update(retried=True, chosen=None, offered=[], prefix="Sorry, that time has just been taken.")
        st["next"] = "find_options"
        st["awaiting"] = "preference"
        st["nlu"] = NLUResult(intent="give_info").model_dump(mode="json")
        return
    _flag(st, "action_blocked")
    _reset_task(st)
    _ask(st, "Sorry, I'm not able to do that over the phone. I can take a message so the team can sort it out. "
             "What would you like me to pass on?", "message_body")


@_node
def faq(st: CallState, d: Deps) -> None:
    r = _nlu(st)
    answer = knowledge.answer(d.s, st["clinic_id"], r.question or st["user_text"])
    if answer:
        _event(st, "faq_answered")
        if st.get("awaiting") in TASK_STAGES:
            _say(st, f"{answer} {st['last_question']}")
            st["next"] = END
        else:
            _anything_else(st, answer)
        return
    _event(st, "faq_unknown")
    _ask(st, "I'm not sure about that one, I'm afraid. Would you like me to take a message so someone can call you back?", "offer_message")


@_node
def take_message(st: CallState, d: Deps) -> None:
    if st.get("awaiting") == "message_body":
        name = st.get("verified_name") or " ".join(filter(None, [st.get("identity", {}).get("first_name"), st.get("identity", {}).get("last_name")])) or None
        d.s.add(Message(clinic_id=st["clinic_id"], call_id=st["call_id"], caller_name=name, caller_phone=st.get("caller_phone"),
                        body=st["user_text"], urgent="redflag" in st.get("flags", [])))
        _event(st, "message_taken")
        if st.get("llm_down"):
            _say(st, f"Thanks, I've passed that on and someone will call you back as soon as they can. Thanks for calling {st['clinic_name']}. Goodbye.")
            st.update(status="ended", awaiting=None, next=END)
            return
        _anything_else(st, "Thanks, I've passed that on, and someone will call you back as soon as they can.")
        return
    if st.get("llm_down"):
        _ask(st, "Sorry, I'm having some technical trouble. Let me take a message and the team will call you back. "
                 "What would you like me to pass on?", "message_body")
        return
    _ask(st, "Of course. What message would you like me to pass on?", "message_body")


@_node
def handoff(st: CallState, d: Deps) -> None:
    clinic = d.s.get(Clinic, st["clinic_id"])
    window = availability.opening_window(d.s, clinic, clock.now().date()) if clinic else None
    open_now = bool(window and window[0] <= clock.now() < window[1])
    if clinic and clinic.transfer_number and open_now:
        HANDOFFS.labels("requested").inc()
        _event(st, "handoff")
        _say(st, "Of course, I'll put you through to the team now. One moment please.")
        st.update(transfer=True, status="handoff", awaiting=None, next=END)
        return
    HANDOFFS.labels("unavailable").inc()
    _event(st, "handoff_unavailable")
    _ask(st, "The team aren't available to take calls right now, but I can take a message and someone will call you back. "
             "What would you like me to pass on?", "message_body")


@_node
def goodbye(st: CallState, d: Deps) -> None:
    _say(st, f"Thanks for calling {st['clinic_name']}. Goodbye!")
    st.update(status="ended", awaiting=None, next=END)


@_node
def clarify(st: CallState, d: Deps) -> None:
    r = _nlu(st)
    if r.intent in ("greeting", "affirm", "thanks") and not st.get("task"):
        _ask(st, "What can I help you with today?", "task")
        return
    st["unknown_count"] = st.get("unknown_count", 0) + 1
    st["confidence"] = round(max(0.0, st.get("confidence", 1.0) - 0.15), 3)
    _event(st, "not_understood")
    if st["unknown_count"] >= 3 or st["confidence"] < 0.35:
        _give_up(st)
        return
    if st.get("awaiting") in TASK_STAGES:
        _ask(st, "Sorry, I didn't quite catch that. " + st["last_question"].removeprefix("Sorry, I didn't quite catch that. "), st["awaiting"])
        return
    _ask(st, "Sorry, I didn't catch that. I can book, move or cancel appointments, or answer questions about the clinic. "
             "What would you like to do?", "task")


def _give_up(st: CallState) -> None:
    """Confidence has collapsed: stop guessing and get a human involved."""
    _flag(st, "low_confidence")
    HANDOFFS.labels("low_confidence").inc()
    _reset_task(st)
    _ask(st, "Sorry, I'm having trouble understanding. Let me take a message for the team and someone will call you back. "
             "What would you like me to pass on?", "message_body")


# ---------------------------------------------------------------- graph


def _goto(state: CallState) -> str:
    return state.get("next") or END


def build_graph():
    g = StateGraph(CallState)
    for fn in (screen, understand, verify, find_options, confirm, act, faq, take_message, handoff, goodbye, clarify):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "screen")
    g.add_conditional_edges("screen", _goto, ["understand", END])
    g.add_conditional_edges("understand", _goto, ["verify", "faq", "take_message", "handoff", "goodbye", "clarify", END])
    g.add_conditional_edges("verify", _goto, ["find_options", "confirm", END])
    g.add_conditional_edges("find_options", _goto, ["confirm", END])
    g.add_conditional_edges("confirm", _goto, ["act", "find_options", END])
    g.add_conditional_edges("act", _goto, ["find_options", END])
    for terminal in ("faq", "take_message", "handoff", "goodbye", "clarify"):
        g.add_edge(terminal, END)
    return g.compile()


GRAPH = build_graph()


def summarize(st: CallState) -> str:
    """Running summary for staff takeover, built from state (not generated), so it can't hallucinate."""
    who = st.get("verified_name") or "Unverified caller"
    if st.get("verified_name"):
        who += " (verified)"
    parts = [f"Caller: {who}."]
    if st.get("task"):
        parts.append(f"Wants to {st['task']} an appointment.")
    if st.get("target_desc"):
        parts.append(f"Existing appointment: {st['target_desc']}.")
    if st.get("offered"):
        parts.append(f"Offered: {_options_text(st['offered'])}.")
    if st.get("chosen"):
        sl = Slot.from_dict(st["chosen"])
        parts.append(f"Chosen: {speak_datetime(sl.start)} with {_first(sl.practitioner_name)} (awaiting confirmation).")
    done = [e for e in st.get("events", []) if e in ("booked", "rescheduled", "cancelled", "message_taken", "forgotten")]
    if done:
        parts.append("Done so far: " + ", ".join(done) + ".")
    if st.get("flags"):
        parts.append("Flags: " + ", ".join(st["flags"]) + ".")
    parts.append(f"Waiting for: {st.get('awaiting') or 'nothing'}.")
    return " ".join(parts)
