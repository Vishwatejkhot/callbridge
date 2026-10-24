from datetime import date

import pytest

from callbridge.agent.nlu import NLUContext, RuleNLU, normalise
from callbridge.agent.prompts import PromptConfig

TODAY = date(2026, 10, 19)


def parse(text, awaiting="task", config=None):
    return RuleNLU().parse(text, NLUContext(TODAY, awaiting, ["Tom", "Priya", "Ellen", "Jake"], config or PromptConfig()))


@pytest.mark.parametrize("text,intent", [
    ("I'd like to book a physio appointment", "book"),
    ("Can I make a sports massage with Jake please", "book"),
    ("I need to move my appointment", "reschedule"),
    ("Can I change my booking to Friday", "reschedule"),
    ("I need to cancel my appointment", "cancel"),
    ("I won't be able to make it on Tuesday", "cancel"),
    ("Can I speak to a real person", "human"),
    ("Could you take a message please", "message"),
    ("Please delete my data", "forget"),
    ("What time do you open on Saturday?", "question"),
    ("yes please", "affirm"),
    ("no thanks", "deny"),
    ("no, that's all, bye", "goodbye"),
])
def test_intents(text, intent):
    assert parse(text).intent == intent


def test_identity_turn_extracts_name_and_dob_but_no_day():
    r = parse("Yes, it's Sarah Jones, 12th of March 1985", awaiting="identity")
    assert (r.first_name, r.last_name, r.dob, r.day) == ("Sarah", "Jones", date(1985, 3, 12), None)


def test_lowercase_transcript_names_only_when_asked():
    assert parse("it's olivia usman", awaiting="identity").first_name == "Olivia"
    assert parse("it's fine", awaiting="identity").first_name is None


def test_apostrophe_names():
    r = parse("It's Rosa O'Brien", awaiting="identity")
    assert (r.first_name, r.last_name) == ("Rosa", "O'Brien")


def test_disfluencies_are_normalised():
    assert normalise("I I um need to, sorry, it's it's my back") == "I need to, it's my back"


def test_improver_phrases_take_priority():
    cfg = PromptConfig(intent_phrases={"book": ["squeeze me in"]})
    assert parse("could you squeeze me in this week", config=cfg).intent == "book"
    assert parse("could you squeeze me in this week").intent != "book"
