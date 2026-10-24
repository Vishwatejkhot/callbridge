# Architecture

```
Patient's phone
   │  (clinic number forwards here when nobody answers / after hours)
   ▼
Twilio ── POST /twilio/voice ──► TwiML <Connect><Stream wss://…/ws/twilio>
   │                                   action=/twilio/after-stream (voicemail if we die)
   ▼
Voice pipeline (Pipecat, callbridge/voice/pipeline.py)
   Silero VAD → Deepgram nova-3 (streaming, en-GB) → Smart Turn v3 (end-of-thought)
        → CallbridgeAgent → Cartesia TTS → Twilio            (barge-in = interruption frames)
   │
   ▼
CallSession (callbridge/agent/session.py): one per call, transport-agnostic
   │  runs one LangGraph pass per caller turn
   ▼
LangGraph (callbridge/agent/graph.py)
   screen ─► understand ─► verify ─► find_options ─► confirm ─► act
     │            │                                              │
     │            └► faq │ take_message │ handoff │ goodbye │ clarify
     │                                                           ▼
     └ red flags / injection, BEFORE any model     tools.booking.propose_and_execute
                                                   │
                                                   ▼
                                        SAFETY CHECKER (plain Python)
                                        → audit_log → Postgres (RLS per clinic) → SMS
```

## Who does what

| Layer | Decides | Never does |
|---|---|---|
| `safety.redflags`, `safety.injection` | 999/111 routing, refusals | call an LLM |
| `agent.llm` (Claude → GPT, Groq/rules for easy turns) | *what the caller meant* (intent + phrases) | write a reply, touch data |
| `agent.dates` | what "next Tuesday" / "half three" means | guess: reads back the full date |
| `agent.graph` | the next question, from verified data | free-text generation |
| `safety.checker` | whether a proposed action is allowed | trust anything the model said |
| `tools.booking` | executes allowed actions, sends SMS | run without a checker verdict |

The LLM's output is a `NLUResult`: an intent and some phrases. Every word the caller hears is written
by code from database facts. So the worst a hijacked model can do is *misunderstand*. It cannot book
the wrong thing (the checker), say the wrong thing (templated replies) or leak data (it never sees it).

## Speed

* **Model routing:** "yes", "no thanks" and "bye" are parsed by rules (or Groq) in about 1 ms. Only real
  understanding goes to Claude (`claude-opus-5-5`, effort `low`, prompt-cached instructions).
* **Speculative understanding:** each final transcript chunk is understood while the caller is still
  talking (`CallSession.prefetch`). If the finished utterance matches, the turn skips the LLM round trip.
  If the caller changed direction, the draft is discarded.
* **Smart turn detection:** Smart Turn v3 listens to *how* a sentence ends, so we don't wait for a long
  silence and don't cut people off mid-thought.
* **Pre-rendered greeting:** the opening line is synthesised once per clinic and cached.

Measure the reply delay with `callbridge_voice_response_seconds` (caller stops → first bot audio).
The target is p95 < 800 ms (docs/slos.md).

## Never silent

| Failure | What the caller hears |
|---|---|
| Claude down | GPT takes the turn (circuit breaker, 4 s timeout) |
| All LLMs down | "Sorry, I'm having some technical trouble. Let me take a message…" |
| Turn crashes | Same message-taking fallback |
| Speech pipeline / websocket dies | Twilio requests `/twilio/after-stream` → Twilio's own TTS + voicemail |
| Daily LLM budget hit | Switches to offline parsing, still works for common requests |
| Clinic turns AI off | Calls go straight to staff (or voicemail) |

## Self-improvement loop

```
calls.failure_reasons ─► improver.testgen (redact, synthetic persona, rephrase-aligned labels)
   ─► test_cases ─► improver.agent (Claude; heuristic offline) proposes a config change
   ─► eval gate: must pass the new tests AND the full suite, with 0 safety failures
   ─► shadow (candidate understands live turns silently; agreement recorded)
   ─► human approval (`callbridge improve approve vN --by you`)
   ─► canary 10% (hash of call id) ─► evaluate every 10 min ─► 100% or automatic rollback
```

The improver can only change `PromptConfig`: intent phrases, the understanding instructions and a few
policies. It cannot touch code, the checker, tools or permissions.

## Data

Postgres 17 on RDS (eu-west-2). Every clinic-owned table has `clinic_id` and a row-level security policy
keyed on `app.clinic_id`, which is set per transaction. The API connects as `callbridge_app`, which has
no BYPASSRLS. Cross-clinic jobs connect as `callbridge_ops`. The application also filters by
`clinic_id` everywhere (defence in depth; SQLite dev databases behave the same way).
