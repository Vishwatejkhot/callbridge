"""Understanding a caller utterance -> intent + entities.

`RuleNLU` is a fast, deterministic parser. It serves three roles: the "easy turn" path of the model
router (yes / no / thanks never need a big model), the offline provider for CI and evals, and a
last-resort parser for entities. LLM providers (agent.llm) produce the same NLUResult."""

import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel

from callbridge.agent import dates
from callbridge.agent.prompts import PromptConfig

INTENTS = ["book", "reschedule", "cancel", "question", "affirm", "deny", "human", "message", "goodbye", "thanks",
           "forget", "give_info", "greeting", "unknown"]
EASY_INTENTS = {"affirm", "deny", "thanks", "goodbye", "greeting"}
SERVICES = {"physio": "physio", "physiotherap": "physio", "osteo": "osteopathy", "massage": "sports massage"}


class NLUResult(BaseModel):
    intent: str = "unknown"
    first_name: str | None = None
    last_name: str | None = None
    dob: date | None = None
    day: date | None = None
    time: str | None = None  # HH:MM
    daypart: str | None = None
    practitioner: str | None = None  # first name as spoken
    service: str | None = None
    choice: int | None = None  # 1-based; -1 = last
    any_time: bool = False
    question: str | None = None
    confidence: float = 0.5
    source: str = "rules"


@dataclass
class NLUContext:
    today: date
    awaiting: str | None
    practitioner_names: list[str]
    config: PromptConfig
    last_bot_utterance: str = ""
    offered_count: int = 0
    extra: dict = field(default_factory=dict)


FILLERS = r"\b(?:um+|uh+|erm+|er|ah+|hmm+|like|you know|i mean|sort of|kind of|basically|actually|so yeah|sorry|so|well)\b,?"

_PATTERNS: list[tuple[str, str]] = [
    ("forget", r"\b(?:delete|remove|erase|wipe) (?:all )?(?:my|me|of my) ?(?:data|details|information|records?)|\bforget (?:me|my)\b|right to (?:be forgotten|erasure)"),
    ("human", r"\b(?:speak|talk|put me through) to (?:a |an |the )?(?:real |actual )?(?:human|person|someone|somebody|receptionist|member of staff|staff|team|physio directly)|\breal person\b|\boperator\b|\b(?:are you|is this) a (?:robot|bot|machine)\b.*\bperson\b"),
    ("message", r"\b(?:leave|take|pass on) a message\b|\bcall me back\b|\bring me back\b|\bget someone to (?:call|ring)\b"),
    ("cancel", r"\bcancel\b|\bcan'?t (?:make|come to) (?:it|my appointment|the appointment)\b|\bwon'?t be able to (?:make|come)\b|\bcall off\b"),
    ("reschedule", r"\b(?:move|moving|reschedule|rearrange|re-arrange|change|changing|swap|push back|bring forward|postpone)\b.{0,30}\b(?:appointment|booking|session|it|time|day)\b|\bdifferent (?:day|time)\b|\breschedule\b"),
    ("book", r"\b(?:book|booking|make an? appointment|make an? (?:[\w-]+ ){0,2}(?:appointment|session|massage|assessment)|(?:need|want|like|after|get) (?:a|an) (?:[\w-]+ ){0,2}(?:appointment|session|massage|assessment)|new appointment|an appointment|appointment with|see (?:a |the |someone|tom|priya|ellen|jake|a physio)|come in|get (?:seen|in)|first session|assessment|slot)\b"),
    ("goodbye", r"\b(?:bye|goodbye|that'?s (?:all|it|everything)|nothing else|no,? that'?s (?:all|it|fine)|i'?m (?:all )?(?:done|good|sorted)|have a (?:good|nice) day)\b"),
    ("thanks", r"^(?:ok(?:ay)?,? )?(?:thanks|thank you|cheers|ta|brilliant,? thanks)\b"),
    ("affirm", r"^(?:yes|yeah|yep|yup|yes please|sure|correct|that'?s (?:right|correct|fine|great|perfect)|perfect|great|ok(?:ay)?|please do|go ahead|sounds (?:good|great)|lovely|that works|fine|absolutely|definitely|please|right|of course|do it|book it|that'?d be (?:great|lovely|good)|that one)\b"),
    ("deny", r"^(?:no|nope|nah|not really|no thanks|no thank you|neither|none of (?:those|them)|wrong|that'?s (?:wrong|not right)|incorrect|don'?t)\b"),
    ("greeting", r"^(?:hi|hello|hey|good (?:morning|afternoon|evening))\b[\s,.!]*$"),
]

_QUESTION = re.compile(
    r"\?$|^(?:what|when|where|how|which|who|do you|does|are you|is there|is it|can i|could you tell|could i|will i|have you got)\b"
    r"|\b(?:opening hours|open on|park|parking|how much|price|cost|address|insurance|accessib|wheelchair|pay(?:ment)?)\b")


def normalise(text: str) -> str:
    t = re.sub(FILLERS, " ", text, flags=re.I)
    t = re.sub(r"\b([\w']+)(?:,?\s+\1\b)+", r"\1", t, flags=re.I)  # "I I need", "it's it's" -> one
    t = re.sub(r"\s+", " ", t).strip(" ,.")
    return t


class RuleNLU:
    def parse(self, text: str, ctx: NLUContext) -> NLUResult:
        raw = normalise(text)
        t = raw.lower()
        r = NLUResult(source="rules")
        cfg = ctx.config

        r.dob = dates.parse_full_date(t) if re.search(r"\b(?:19|20)\d{2}\b|\d{1,2}/\d{1,2}/\d{2}\b", t) else None
        if r.dob and r.dob.year > ctx.today.year - 1:  # a full date this year or later is an appointment day
            r.day, r.dob = r.dob, None
        t_wo_dob = dates.strip_full_dates(t) if r.dob else t  # "12th March 1985" must not also become a day
        r.day = r.day or dates.parse_day(t_wo_dob, ctx.today, cfg.next_weekday_policy)
        r.time = dates.parse_time(t_wo_dob)
        r.daypart = None if r.time else dates.parse_daypart(t_wo_dob)
        r.any_time = any(p in t for p in cfg.any_time_phrases)
        for key, svc in SERVICES.items():
            if key in t:
                r.service = svc
        for name in ctx.practitioner_names:
            if re.search(rf"\b{re.escape(name.lower())}\b", t):
                r.practitioner = name
        r.choice = self._choice(t, ctx)
        r.first_name, r.last_name = self._name(raw, ctx)

        r.intent, r.confidence = self._intent(t, cfg)
        if r.intent == "question":
            r.question = raw
        has_entities = any([r.dob, r.day, r.time, r.daypart, r.first_name, r.choice, r.practitioner, r.any_time])
        if r.intent == "unknown" and has_entities:
            r.intent, r.confidence = "give_info", 0.85
        if r.intent in ("affirm", "deny") and has_entities and ctx.awaiting in ("slot_choice", "preference", "identity", "which_booking"):
            r.intent = "give_info" if r.intent == "affirm" or not r.choice else r.intent
        return r

    def _intent(self, t: str, cfg: PromptConfig) -> tuple[str, float]:
        for intent, phrases in cfg.intent_phrases.items():
            if any(re.search(rf"\b{re.escape(p.lower())}\b", t) for p in phrases):
                return intent, 0.9
        for intent, pattern in _PATTERNS:
            if intent in ("affirm", "deny", "thanks", "greeting") and re.search(_PATTERNS_DICT["book"], t) and len(t.split()) > 6:
                continue  # "yes, I'd like to book..." is a booking, not a bare yes
            if re.search(pattern, t):
                if intent == "book" and _QUESTION.search(t) and re.search(r"\b(?:how much|price|cost|parking)\b", t):
                    return "question", 0.8
                return intent, 0.95 if intent in ("affirm", "deny", "thanks", "goodbye") and len(t.split()) <= 5 else 0.85
        if _QUESTION.search(t):
            return "question", 0.8
        return "unknown", 0.3

    def _choice(self, t: str, ctx: NLUContext) -> int | None:
        if re.search(r"\b(?:first|earlier|earliest|former|first one|the one before)\b", t) and not re.search(r"\bfirst (?:session|appointment|assessment|available|time)\b", t):
            return 1
        if re.search(r"\b(?:second|last|later|latter|other one|second one)\b", t):
            return -1 if "second" not in t else 2
        if re.search(r"\bthird\b", t):
            return 3
        if re.search(r"\b(?:either|any of them|whichever|either one)\b", t):
            return 1
        return None

    def _name(self, raw: str, ctx: NLUContext) -> tuple[str | None, str | None]:
        name_word = r"[A-Z][a-z]*(?:[A-Z][a-z]+)?(?:[-'][A-Z]?[a-z]+)?"
        intro = r"(?i:my name is|my name's|name is|this is|it'?s|i'?m|i am|speaking|name)"
        m = re.search(rf"\b{intro}\s+({name_word})(?:\s+({name_word}))?", raw)
        if not m and ctx.awaiting == "identity":
            m = re.match(rf"^(?:yes,?\s+|yeah,?\s+|sure,?\s+)?({name_word})\s+({name_word})\b", raw)
            if not m:  # transcripts without capitals: only trust them when we've just asked for a name
                lower = r"[a-z][a-z'-]+"
                m = (re.search(rf"(?i)\b{intro}\s+({lower})(?:\s+({lower}))?", raw)
                     or re.match(rf"(?i)^(?:yes,?\s+|yeah,?\s+|sure,?\s+)?({lower})\s+({lower})\b", raw))
        if not m:
            return None, None
        first, last = m.group(1), m.group(2) if m.lastindex and m.lastindex >= 2 else None
        first, last = first[:1].upper() + first[1:], (last[:1].upper() + last[1:]) if last else None
        bad = {w.lower() for w in dates.WEEKDAYS + dates.MONTHS} | {"calling", "ringing", "looking", "trying", "just",
                                                                    "not", "fine", "good", "sorry", "after", "hoping",
                                                                    "wondering", "here", "afraid", "phoning", "the", "it's", "its", "yes",
                                                                    "yeah", "hi", "hello", "date", "born", "my"}
        if first.lower() in bad or first.lower() in {n.lower() for n in ctx.practitioner_names}:
            return None, None
        if last and last.lower() in bad:
            last = None
        return first, last


_PATTERNS_DICT = dict(_PATTERNS)
