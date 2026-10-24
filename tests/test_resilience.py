import pytest

from callbridge.agent.llm import LLMUnavailable, NLUEngine
from callbridge.agent.nlu import NLUContext
from callbridge.agent.prompts import PromptConfig
from callbridge.config import Settings
from callbridge.resilience import CircuitBreaker, CircuitOpen


class Boom:
    name, model = "anthropic", "claude-opus-5-5"

    def __init__(self):
        self.calls = 0

    def extract(self, text, ctx):
        self.calls += 1
        raise ConnectionError("down")


class Works:
    name, model = "openai", "gpt-5"

    def extract(self, text, ctx):
        return {"intent": "book", "first_name": "", "last_name": "", "dob_phrase": "", "day_phrase": "Friday",
                "time_phrase": "3pm", "daypart": "", "practitioner": "", "service": "physio", "choice": 0,
                "any_time": False, "question": "", "confidence": 0.9}, 0.0001


def engine(*providers):
    e = NLUEngine(Settings(llm_provider="rules"))
    e.chain = list(providers)
    e.breakers = {p.name: CircuitBreaker(p.name) for p in providers}
    return e


def ctx():
    from datetime import date

    return NLUContext(date(2026, 10, 19), "task", ["Tom"], PromptConfig())


def test_falls_back_from_claude_to_gpt():
    r, meta = engine(Boom(), Works()).parse("could I get something on Friday at 3 for my knee", ctx())
    assert r.intent == "book" and r.time == "15:00" and r.day.weekday() == 4
    assert meta.fallback_used and meta.model == "gpt-5"


def test_easy_turns_skip_the_big_model():
    boom = Boom()
    r, meta = engine(boom).parse("yes please", ctx())
    assert r.intent == "affirm" and meta.tier == "fast" and boom.calls == 0


def test_all_providers_down_raises():
    with pytest.raises(LLMUnavailable):
        engine(Boom()).parse("could I get something on Friday", ctx())


def test_circuit_breaker_opens_then_half_opens():
    cb = CircuitBreaker("x", failure_threshold=2, reset_after_s=0.0)
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert cb.state == "half_open"  # reset_after 0 => immediately half-open
    assert cb.call(lambda: 1) == 1 and cb.state == "closed"
    cb2 = CircuitBreaker("y", failure_threshold=1, reset_after_s=60)
    with pytest.raises(ValueError):
        cb2.call(lambda: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(CircuitOpen):
        cb2.call(lambda: 1)


def test_llm_outage_mid_call_takes_a_message(call):
    s = call(None, engine=engine(Boom()))
    r = s.turn("I was hoping to sort out something about my knee next week")
    assert "take a message" in r.reply
    r = s.turn("Please call me back on this number")
    assert "passed that on" in r.reply and r.ended
