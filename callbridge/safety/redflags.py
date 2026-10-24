"""Medical red-flag screening. Runs on every caller utterance BEFORE any LLM sees it.

Deliberately over-sensitive: a false positive costs one unnecessary safety message, a false
negative could cost a life. This is not diagnosis; it only routes the caller to 999 / NHS 111."""

import re

RED_FLAGS: dict[str, list[str]] = {
    "chest_pain": [r"chest\W+(?:\w+\W+){0,3}?(?:pain|pains|hurts?|hurting|tight(?:ness)?|pressure|heavy|crushing|squeez)",
                   r"pain in (?:my|the) chest", r"heart attack", r"tight(?:ness)? (?:in|across) (?:my|the) chest"],
    "breathing": [r"can'?t\W+(?:\w+\W+){0,2}?breathe", r"cannot breathe", r"not breathing", r"(?:struggling|difficulty|trouble|hard) (?:to )?breath(?:e|ing)",
                  r"short(?:ness)? of breath", r"choking"],
    "stroke": [r"face (?:is )?droop", r"slurred speech", r"can'?t (?:move|feel) (?:my )?(?:arm|leg|face|side)", r"\bstroke\b",
               r"sudden(?:ly)? numb"],
    "bleeding": [r"(?:severe|heavy|lots of|won'?t stop) bleed", r"bleeding (?:heavily|a lot|badly|won'?t stop)", r"blood everywhere"],
    "consciousness": [r"passed out", r"\bfaint(?:ed|ing)\b", r"unconscious", r"collapsed", r"blacked out", r"\bseizure\b", r"having (?:a )?fits?\b"],
    "cauda_equina": [r"numb(?:ness)? (?:around|in) (?:my )?(?:groin|bottom|saddle|genitals)", r"saddle numbness",
                     r"(?:can'?t|cannot|unable to) (?:control|hold) (?:my )?(?:bladder|bowel|wee|pee|poo)",
                     r"(?:lost|loss of) (?:bladder|bowel) control", r"\bincontinen"],
    "head_injury": [r"(?:hit|banged|bashed) (?:my )?head.*(?:sick|vomit|confus|dizzy|blur)", r"head injury"],
    "severe_pain": [r"worst (?:pain|headache) (?:of my life|ever)", r"thunderclap", r"\bunbearable pain\b", r"agony"],
    "anaphylaxis": [r"(?:throat|tongue|lips?) (?:is |are )?swell", r"anaphyla", r"allergic reaction"],
    "self_harm": [r"kill (?:myself|me)", r"end (?:it all|my life)", r"suicid", r"self[- ]harm", r"hurt myself", r"don'?t want to (?:live|be here)"],
    "sepsis": [r"\bsepsis\b", r"(?:mottled|blue) skin", r"\bfever\b.*(?:confus|rash)"],
}

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in RED_FLAGS.items()}

EMERGENCY_REPLY = (
    "I'm really sorry to hear that. I can't give medical advice, but if it's severe or getting worse, "
    "please hang up and call 999 now. Otherwise, NHS 111 can advise you. I'm flagging this for the clinic team straight away."
)
SELF_HARM_REPLY = (
    "I'm really sorry you're feeling like this. Please call 999 now if you're in danger. You can also call "
    "Samaritans free, any time, on 116 123. I'm flagging this for the clinic team straight away."
)


def detect(text: str) -> str | None:
    """Return the red-flag category, or None."""
    for category, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            return category
    return None


def reply_for(category: str) -> str:
    return SELF_HARM_REPLY if category == "self_harm" else EMERGENCY_REPLY
