"""`callbridge improve ...`: the self-improving loop, end to end.

  run      failed calls -> redacted test cases -> improver proposes a fix -> fix must pass the new tests
           AND the whole existing suite (eval gate) -> shadow mode on live calls
  approve  a human approves -> canary on 10% of calls
  evaluate promote to 100%, or roll back automatically if the numbers drop (run on a schedule)"""

import json
from pathlib import Path

from sqlalchemy import select

from callbridge import db
from callbridge.agent.prompts import PromptConfig
from callbridge.db.models import PromptVersion, TestCase
from callbridge.evals import runner
from callbridge.improver import agent, rollout, testgen

REGRESSIONS = Path("evals/scenarios/regressions.jsonl")


def _next_version(s) -> str:
    nums = [int(v[1:]) for v in s.scalars(select(PromptVersion.version)) if v[1:].isdigit()]
    return f"v{max(nums, default=1) + 1}"


def export_regressions() -> int:
    """Write labelled regression tests into the suite file that CI runs on every pull request."""
    with db.session() as s:
        cases = [tc.scenario for tc in s.scalars(select(TestCase).where(TestCase.status.in_(["in_suite", "fixed"])))]
    REGRESSIONS.parent.mkdir(parents=True, exist_ok=True)
    REGRESSIONS.write_text("".join(json.dumps(c) + "\n" for c in cases))
    return len(cases)


def improve(days: int = 7) -> dict:
    with db.session() as s:
        new_cases = testgen.generate(s, days)
        report = {"new_test_cases": len(new_cases), "needs_label": sum(tc.status == "new" for tc in new_cases)}
        open_cases = [tc.scenario for tc in s.scalars(select(TestCase).where(TestCase.status == "in_suite"))]
        version = _next_version(s)
    report["suite_regressions"] = export_regressions()
    if not open_cases:
        report["result"] = "no failures to learn from"
        return report

    current, _, _ = rollout._configs()
    before, _ = runner.run_suite(open_cases, config=current)
    candidate, rationale = agent.propose(current, open_cases, version)
    report.update(rationale=rationale, open_cases_passing_before=before["pass_rate"])
    if candidate is None:
        report["result"] = "improver found no fix"
        return report

    on_new, _ = runner.run_suite(open_cases, config=candidate)
    core = runner.load(None)
    core = [sc for sc in core if sc["category"] != "regression"]
    full, _ = runner.run_suite(core, config=candidate)
    baseline_path = Path("evals/baseline.json")
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    problems = runner.gate(full, baseline)
    if on_new["pass_rate"] < 1.0:
        problems.append(f"fix only passes {on_new['pass_rate']:.0%} of the new failure tests")
    passed = not problems
    rollout.submit(candidate, parent=current.version, rationale=rationale)
    rollout.record_gate(candidate.version, {"new_tests": on_new, "full_suite": full, "problems": problems}, passed)
    if passed:
        rollout.start_shadow(candidate.version)
        with db.session() as s:
            for tc in s.scalars(select(TestCase).where(TestCase.status == "in_suite")):
                tc.status = "fixed"
        export_regressions()
    report.update(candidate=candidate.version, gate_passed=passed, problems=problems,
                  new_tests_pass_rate=on_new["pass_rate"], full_suite_pass_rate=full["pass_rate"],
                  safety_failures=full["safety_failures"],
                  result=f"{candidate.version} is in shadow mode; approve with: callbridge improve approve {candidate.version} --by <you>"
                  if passed else f"{candidate.version} rejected by the eval gate")
    return report


def status() -> list[dict]:
    rollout.flush_counters()
    with db.session() as s:
        return [{"version": v.version, "status": v.status, "parent": v.parent, "approved_by": v.approved_by,
                 "stats": v.shadow_stats, "rationale": (v.rationale or "")[:160]}
                for v in s.scalars(select(PromptVersion).order_by(PromptVersion.id))]


def main(args) -> int:
    db.init_db()
    if args.action == "run":
        print(json.dumps(improve(args.days), indent=1, default=str))
    elif args.action == "status":
        print(json.dumps(status(), indent=1, default=str))
    elif args.action == "approve":
        try:
            rollout.approve(args.version, args.by, require_shadow=not args.skip_shadow)
        except rollout.RolloutError as e:
            print(f"not approved: {e}")
            return 1
        print(f"{args.version} approved by {args.by}: now serving {rollout.CANARY_PERCENT}% of calls (canary)")
    elif args.action == "evaluate":
        for action in rollout.evaluate() or ["no change"]:
            print(action)
    elif args.action == "rollback":
        rollout.rollback(args.version)
        print(f"rolled back {args.version}")
    return 0


__all__ = ["improve", "status", "main", "PromptConfig"]
