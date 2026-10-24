# Service level objectives

| SLO | Target | Measured by |
|---|---|---|
| Reply delay (caller stops → AI starts speaking) | p95 < 800 ms | `callbridge_voice_response_seconds` |
| Availability (Twilio webhook + stream answered) | 99.5% monthly | Better Stack HTTP check on `/healthz` + ALB 5xx |
| Calls that fail (crash, silence, no outcome) | < 1% | `callbridge_call_outcomes_total`, `turn_error` events |
| Red-flag calls escalated (999/111 + urgent staff message) | 100% | eval suite (CI), `callbridge_redflags_total`, weekly review |
| Wrong bookings | 0 | audit log review, checker rejections, staff corrections |

Alert rules: `infra/monitoring/alerts.yml`. Error budget for 99.5% is about 3.6 hours a month. When it is
spent, feature work stops and reliability work starts.

## Numbers log (real numbers only)

Fill this weekly. Never copy numbers from the plan's examples.

| Week | Clinics live | Calls | Handled w/o staff | Uptime | p95 reply | £/min | Fixes shipped | Incidents |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

Measured so far (development only, not production):
* Eval suite in rules mode: see `evals/baseline.json` (300 scenarios). This is in-distribution; see ADR 0003.
* Local text-simulator smoke test: 10 concurrent calls, 0 errors, p95 turn about 30 ms (laptop, SQLite,
  no LLM). This says nothing about production voice latency.
