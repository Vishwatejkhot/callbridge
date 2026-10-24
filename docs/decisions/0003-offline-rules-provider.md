# 0003: A deterministic rules parser alongside the LLMs

**Status:** accepted

`agent/nlu.py: RuleNLU` handles three jobs:
1. **Fast path** for easy turns (yes/no/thanks/bye): about 1 ms, free, no network.
2. **Offline provider** (`CALLBRIDGE_LLM_PROVIDER=rules`) for CI, evals and local development. The
   300-scenario gate runs on every PR in about a minute and costs nothing.
3. **Degraded mode** when the daily cost cap is hit.

Caveat (be honest about it): the core scenario suite was developed alongside the rules parser, so the
rules-mode pass rate is *in-distribution*. The numbers that matter for real calls come from the
`eval-gate-llm` CI job (Claude doing the understanding), `--llm-patient` runs (Claude playing more varied
callers), and production failure tests in `evals/scenarios/regressions.jsonl`.
