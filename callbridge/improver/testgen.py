"""Failed calls -> regression test cases, with personal data removed.

Transcripts are already PII-redacted when stored ([NAME], [DATE], [PHONE]...). Here the placeholders
are swapped for an invented persona so the test is runnable, and the expected behaviour is derived
from *why* the call failed. Labels come from "rephrase alignment": when the caller wasn't understood
and then said it differently and *was* understood, the second intent is the label for the first."""

import random
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.db.models import Call, TestCase

AUTO_LABELLED = {"misunderstanding", "low_confidence", "action_blocked", "system_error"}
FIRST = ["Alex", "Jordan", "Robin", "Casey", "Morgan", "Taylor", "Jamie", "Riley"]
LAST = ["Smith", "Taylor", "Brown", "Wilson", "Evans", "Thomas", "Roberts", "Walker"]


def failed_calls(s: Session, days: int) -> list[Call]:
    since = clock.now() - timedelta(days=days)
    done = set(s.scalars(select(TestCase.source_call_id)))
    rows = s.scalars(select(Call).where(Call.started_at >= since, Call.ended_at.is_not(None)))
    return [c for c in rows if c.failure_reasons and c.id not in done]


def _synthetic_persona(seed: str) -> dict:
    rng = random.Random(seed)
    return {"first_name": rng.choice(FIRST), "last_name": rng.choice(LAST),
            "dob": f"{rng.randint(1950, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "phone": f"+447700900{rng.randint(100, 999)}"}


def _fill(text: str, persona: dict) -> str:
    y, m, d = persona["dob"].split("-")
    text = text.replace("[NAME]", persona["first_name"]).replace("[DATE]", f"{int(d)}/{int(m)}/{y}")
    return re.sub(r"\[(?:PHONE|EMAIL|POSTCODE|NI_NUMBER|NHS_NUMBER|CARD)\]", "", text).strip()


def to_scenario(call: Call) -> dict:
    persona = _synthetic_persona(call.id)
    turns = call.transcript or []
    script = [_fill(t["text"], persona) for t in turns if t["role"] == "caller"]
    expected_intents: dict[str, str] = {}
    caller_turns = [t for t in turns if t["role"] == "caller"]
    for i, t in enumerate(caller_turns):
        if t.get("intent") in ("unknown", None) and i + 1 < len(caller_turns):
            nxt = caller_turns[i + 1].get("intent")
            if nxt and nxt not in ("unknown", "give_info", "affirm", "deny", "thanks", "goodbye"):
                expected_intents[_fill(t["text"], persona)] = nxt
    reasons = set(call.failure_reasons or [])
    expect: dict = {}
    if reasons & {"misunderstanding", "low_confidence"}:
        # Once a fix changes the first reply, later replayed lines answer questions that are no longer
        # asked, so a labelled replay asserts only on the labelled utterances.
        if expected_intents:
            expect["expected_intents"] = expected_intents
        else:
            expect["max_not_understood"] = 0
    if "action_blocked" in reasons:
        expect["no_blocked_actions"] = True
    if "system_error" in reasons:
        expect["no_turn_errors"] = True
    if not expect:
        expect["needs_label"] = True
    if call.staff_correction:
        expect["staff_note"] = call.staff_correction
    return {
        "id": f"regression-{call.id[-10:]}", "category": "regression", "now": call.started_at.isoformat(),
        "caller_phone": persona["phone"], "persona": {**persona, "goal": "replay", "opening": script[0] if script else ""},
        "script": script, "fixtures": {"patients": [], "bookings": []}, "expect": expect, "safety_critical": False,
        "source_call_id": call.id, "reasons": sorted(reasons),
    }


def generate(s: Session, days: int) -> list[TestCase]:
    out = []
    for call in failed_calls(s, days):
        sc = to_scenario(call)
        if not sc["script"]:
            continue
        status = "new" if sc["expect"].get("needs_label") else "in_suite"
        tc = TestCase(source_call_id=call.id, reason=",".join(sc["reasons"]), scenario=sc, status=status)
        s.add(tc)
        out.append(tc)
    s.flush()
    return out
