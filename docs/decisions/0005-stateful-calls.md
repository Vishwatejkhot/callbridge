# 0005: Call state lives in the process that holds the websocket

**Status:** accepted

A Twilio media stream is one websocket pinned to one API task for the life of the call. `CallSession`
and the live-call registry are therefore in-memory, and the durable record (redacted transcript,
outcome, events) is written to Postgres at the end of the call.

Consequences:
* Deploys must drain: ECS `stopTimeout` 120 s and ALB deregistration delay 120 s let calls finish.
* The dashboard's live view and one-click takeover talk to whichever task has the call. With more than
  one API task, put the dashboard's SSE behind sticky routing, or publish registry events via Postgres
  `LISTEN/NOTIFY` (the next step once a second task is regularly needed; one Fargate task handled the
  local load test comfortably, and see docs/slos.md for the real numbers to collect).
* The text simulator (`/api/sim/...`) has the same affinity requirement. Load tests target one task or
  use sticky sessions.
