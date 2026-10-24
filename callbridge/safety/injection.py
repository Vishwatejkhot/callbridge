"""Screening for manipulation attempts and medical-advice requests.

This is the first line only. The real defence is structural: the LLM never holds other patients'
data, and every action is re-checked by safety.checker against the verified caller."""

import re

_INJECTION = [
    r"ignore (?:all |your |the |any )?(?:previous |prior |above )?(?:instructions|rules|prompt)",
    r"(?:system|developer) prompt", r"you are now", r"pretend (?:to be|you are)", r"act as (?:a|an|the) ",
    r"\bjailbreak\b", r"disregard (?:your|the|all)", r"override (?:your|the) (?:rules|safety)",
    r"developer mode", r"reveal (?:your|the) (?:instructions|prompt|rules)", r"new instructions",
]
_OTHER_PATIENT = [
    r"(?:another|other|a different) (?:patient|person)'?s?",
    r"(?:my|his|her) (?:wife|husband|partner|son|daughter|mum|mom|dad|friend|neighbou?r)'?s? (?:appointment|booking|details|date of birth|address|phone)",
    r"(?:who|which patients?) (?:else )?(?:is|are) (?:booked|coming)",
    r"(?:list|read|tell me) (?:all |the )?(?:patients|bookings|appointments) (?:for|on|today|tomorrow)",
    r"(?:read|tell|give) me (?:\w+ )?(?:\w+'s) (?:appointment|booking|details|address|phone|number|date of birth)",
    r"what time is (?:\w+ )?\w+'s appointment",
    r"(?:address|phone number|date of birth) (?:for|of) (?:a |another |the )?(?:patient|someone)",
    r"\b(?!(?:clinic|practice|surgery|centre|center|physio|your|today|tomorrow|week|monday|tuesday|wednesday|thursday"
    r"|friday|saturday|sunday|day|doctor|it|that|what|who|there|here)'s\b)[a-z]+'s (?:appointment|booking|session|details"
    r"|address|phone(?: number)?|number|date of birth|dob|email|records?)",
    r"\bwhen is (?:my )?(?:wife|husband|partner|son|daughter|mum|mom|dad|friend)",
]
_MEDICAL_ADVICE = [
    r"what(?:'s| is) wrong with (?:me|my)", r"should i (?:take|use|stop|ice|heat|rest|exercise)",
    r"(?:is it|could it be|do you think it'?s) (?:broken|serious|a fracture|a tear|sciatica|arthritis|cancer)",
    r"what (?:painkiller|medication|medicine|tablets?|exercises?) should", r"how (?:much|many) (?:ibuprofen|paracetamol|co-codamol|naproxen)",
    r"diagnos(?:e|is)", r"is (?:it|this) normal (?:to|that|for)",
]

_INJ = [re.compile(p, re.I) for p in _INJECTION]
_OTHER = [re.compile(p, re.I) for p in _OTHER_PATIENT]
_MED = [re.compile(p, re.I) for p in _MEDICAL_ADVICE]

REFUSE_INJECTION = "I can only help with appointments and general questions about the clinic. What can I help you with?"
REFUSE_OTHER_PATIENT = (
    "I'm sorry, I can't share or change anyone else's details. The person themselves can call us, "
    "or I can take a message for the team. Is there anything else I can help you with?"
)
REFUSE_MEDICAL = (
    "I'm not able to give medical advice, I'm afraid. Your practitioner can talk that through with you at your appointment, "
    "or for advice now NHS 111 can help. Would you like to book an appointment?"
)


def classify(text: str) -> str | None:
    """Return 'injection', 'other_patient', 'medical_advice', or None."""
    if any(p.search(text) for p in _INJ):
        return "injection"
    if any(p.search(text) for p in _OTHER):
        return "other_patient"
    if any(p.search(text) for p in _MED):
        return "medical_advice"
    return None


REPLIES = {"injection": REFUSE_INJECTION, "other_patient": REFUSE_OTHER_PATIENT, "medical_advice": REFUSE_MEDICAL}
