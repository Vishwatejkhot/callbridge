"""The self-improving loop, end to end, offline: a real failure becomes a test, the improver proposes
a fix, the fix is gated on every test, goes to shadow, a human approves, canary, then 100%."""

import json

import pytest
from sqlalchemy import select

from callbridge import db
from callbridge.agent.prompts import PromptConfig
from callbridge.db.models import Call, PromptVersion, TestCase
from callbridge.evals import runner
from callbridge.improver import loop, rollout


@pytest.fixture
def small_suite(monkeypatch, tmp_path):
    core = [sc for sc in runner.load(["evals/scenarios/core.jsonl"])][::10]  # 30 scenarios keeps the test quick
    monkeypatch.setattr(runner, "load", lambda paths=None: core)
    monkeypatch.setattr(loop, "REGRESSIONS", tmp_path / "regressions.jsonl")
    rollout.invalidate()
    yield core
    rollout.invalidate()


def _misunderstood_call(call):
    s = call(None)
    s.turn("could you squeeze me in this week")  # not understood...
    s.turn("I want to book an appointment")  # ...rephrased and understood: the label
    s.end("hangup")
    return s.call_id


def test_failure_becomes_test_then_fix_ships_safely(clinic, call, small_suite):
    with db.session() as s:
        s.add(PromptVersion(version="v1", config=PromptConfig().model_dump(exclude={"version"}), status="live"))
    call_id = _misunderstood_call(call)
    with db.session() as s:
        assert "misunderstanding" in s.get(Call, call_id).failure_reasons

    report = loop.improve(days=1)
    assert report["new_test_cases"] == 1, report
    assert report["gate_passed"] is True, (report.get("problems"), report.get("new_tests_pass_rate"))
    assert report["safety_failures"] == 0
    with db.session() as s:
        tc = s.scalar(select(TestCase))
        assert tc.scenario["expect"]["expected_intents"] == {"could you squeeze me in this week": "book"}
        assert "1985" not in json.dumps(tc.scenario)
        v2 = s.scalar(select(PromptVersion).where(PromptVersion.version == "v2"))
        assert v2.status == "shadow" and "could you squeeze me in this week" in v2.config["intent_phrases"]["book"]

    # Shadow evidence is required before a human can approve it.
    with pytest.raises(rollout.RolloutError):
        rollout.approve("v2", "test-engineer")
    for _ in range(rollout.MIN_SHADOW_TURNS):
        rollout.record_shadow("v2", agree=True)
    rollout.approve("v2", "test-engineer")
    assert {v["version"]: v["status"] for v in loop.status()}["v2"] == "canary"

    # Canary does as well as live -> promoted to 100%.
    for _ in range(rollout.MIN_CANARY_CALLS):
        rollout.on_call_finished("canary", "v2", ok=True)
        rollout.on_call_finished("live", "v1", ok=True)
    assert rollout.evaluate() == ["promoted v2 to 100%"]
    assert rollout.choose("any-call")[0].version == "v2"


def test_bad_canary_is_rolled_back_automatically(clinic):
    with db.session() as s:
        s.add(PromptVersion(version="v1", config={}, status="live"))
        s.add(PromptVersion(version="v2", parent="v1", config={}, status="canary"))
    for i in range(rollout.MIN_CANARY_CALLS):
        rollout.on_call_finished("live", "v1", ok=True)
        rollout.on_call_finished("canary", "v2", ok=i % 2 == 0)  # 50% success vs 100%
    assert rollout.evaluate()[0].startswith("rolled back canary v2")
    rollout.invalidate()
    assert rollout.choose("x")[0].version == "v1"


def test_canary_gets_about_ten_percent(clinic):
    with db.session() as s:
        s.add(PromptVersion(version="v1", config={}, status="live"))
        s.add(PromptVersion(version="v2", parent="v1", config={}, status="canary"))
    rollout.invalidate()
    arms = [rollout.choose(f"call-{i}")[1] for i in range(2000)]
    assert 0.07 < arms.count("canary") / len(arms) < 0.13
