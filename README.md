# Callbridge

**A self-improving, safety-verified AI phone receptionist for private clinics.**

When the clinic can't answer, Callbridge does. It books, moves and cancels appointments, answers simple
questions, takes messages, and hands over to a human when it should. It never gives medical advice.

* **The AI proposes, code decides.** The model only *understands* the caller. Plain Python checks every
  action against the verified caller before anything changes, and writes it to an audit log. A tricked
  model can't book the wrong slot or read out someone else's details. ([ADR 0001](docs/decisions/0001-ai-proposes-code-decides.md))
* **It improves itself, safely.** Failed calls become redacted regression tests. An improver proposes a
  fix, which must pass every test, then runs in shadow, gets human approval, goes to a 10% canary, and
  reaches 100% only if the numbers hold. Rollback is automatic.
* **Humans stay in control.** Staff see live calls with AI confidence and alerts, and can take over with
  one click. A ready-made summary means the patient never repeats themselves.

> Status: **pilot-ready, not yet in production.** The numbers in this README are development measurements.
> Real-world numbers go in [docs/slos.md](docs/slos.md) once a clinic is live.

## How a call works

```
Twilio → Pipecat (Silero VAD → Deepgram → Smart Turn v3) → LangGraph brain → Cartesia → Twilio
                                   │
           screen (red flags, injection) → understand (Claude → GPT; rules for easy turns)
           → verify → find options → confirm → SAFETY CHECKER → act → SMS
```

Details: [docs/architecture.md](docs/architecture.md).

## Quickstart (offline, about 2 minutes)

```bash
uv sync --all-extras
uv run callbridge init --demo          # fictional "Harbourside Physio" + staff login
uv run callbridge chat --phone +447700900123   # you are Sarah (DOB 12th March 1985)
```

```
AI:  Hi, you've reached Harbourside Physio. I'm an AI assistant, and this call may be recorded. How can I help?
You: Hi, I need to move my appointment
AI:  Can I just confirm your date of birth?
You: 12th March 1985
AI:  Thanks, Sarah. What day and time would suit you?
You: Thursday afternoon
AI:  Tom has Thursday the 29th of October at 12pm or 2:30pm. Which works better?
You: the later one
AI:  So that's moving your appointment tomorrow at 10am with Tom to Thursday the 29th of October at 2:30pm with Tom. Shall I go ahead?
You: yes please
AI:  Done. You're now booked with Tom on Thursday the 29th of October at 2:30pm. I've sent you a text to confirm. ...
```

The API, dashboard and monitoring:

```bash
uv run callbridge serve                       # API on :8000 (Twilio webhook: /twilio/voice)
cd dashboard && npm install && npm run dev    # staff dashboard on :3000 (demo@harbourside.example / demo-password)
docker compose up --build                     # full stack: Postgres+RLS, API, dashboard, Prometheus, Grafana (:3001)
```

Real phone calls need `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY` and Twilio
credentials in `.env` (see `.env.example`), plus `CALLBRIDGE_LLM_PROVIDER=anthropic`.

## Testing and the CI gate

```bash
uv run pytest -q                        # unit, conversation, API, 50+ adversarial checker tests
uv run callbridge eval --gate -v        # 300 fake-patient calls vs evals/baseline.json
```

Every pull request runs the fake-patient suite. **A merge is blocked** if the pass rate drops, if *any*
safety test fails (red-flag escalation, injection, other patients' data, wrong person, medical advice),
or if latency or cost rise. The suite covers booking (new and returning patients), reschedule, cancel,
change of mind, wrong date of birth, 25 injection attempts, 15 requests for other patients' data,
30 red flags, medical-advice requests, FAQs, messages, handoffs and "delete my data", with ASR-style
noise (fillers, stutters, missing capitals).

Development result in offline rules mode: 300/300 passing, 0 safety failures (`evals/baseline.json`).
This is in-distribution: the rules parser was built against these scenarios
([ADR 0003](docs/decisions/0003-offline-rules-provider.md)). The `eval-gate-llm` CI job and
`--llm-patient` runs (Claude plays the caller) are the honest measure of real-world understanding.

## The improvement loop

```bash
uv run callbridge improve run                     # failed calls -> tests -> proposed fix -> gate -> shadow
uv run callbridge improve status
uv run callbridge improve approve v2 --by you     # human approval -> 10% canary
uv run callbridge improve evaluate                # promote or auto-rollback (scheduled every 10 min)
```

`tests/test_improver.py` runs the whole loop end to end. A caller says "could you squeeze me in this
week", isn't understood, and rephrases. The rephrase labels the failure, the improver teaches the phrase,
the gate passes it with 0 safety failures, then shadow → approval → canary → 100%. A second test shows a
worse canary rolled back automatically.

## Layout

| Path | What |
|---|---|
| `callbridge/voice/` | Pipecat pipeline, Twilio control, never-silent fallback |
| `callbridge/agent/` | LangGraph flow, NLU, model router, date resolution, call session |
| `callbridge/safety/` | Checker, proposals, red flags, injection screening, PII redaction |
| `callbridge/tools/` | Booking tools (+ MCP server: `callbridge mcp`), availability, SMS, identity |
| `callbridge/memory/`, `knowledge/` | Returning callers; clinic FAQ retrieval |
| `callbridge/improver/` | Test generation, improver agent, shadow/canary rollout |
| `callbridge/evals/` | Fake patients, scenario generator, runner + gate |
| `callbridge/api/` | FastAPI: Twilio webhooks, dashboard API, simulator, metrics |
| `dashboard/` | Next.js staff dashboard |
| `loadtest/` | `ramp.py` (10 → 50 → 100 concurrent calls), k6 script |
| `infra/` | Terraform (AWS eu-west-2), Dockerfiles, SQL (RLS), Prometheus/Grafana |
| `docs/` | Architecture, ADRs, security, compliance, SLOs, runbook, postmortems, pilot playbook |

## Deploy

```bash
scripts/deploy.sh staging        # build, push to ECR, terraform apply, wait for stable, smoke test
scripts/deploy.sh production     # only from origin/main, asks for confirmation
```

Before the first real patient call, work through [docs/compliance.md](docs/compliance.md) (ICO, DPA,
DPIA, sub-processors) and [docs/pilot-playbook.md](docs/pilot-playbook.md).
