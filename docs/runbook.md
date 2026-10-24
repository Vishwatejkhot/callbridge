# Runbook

First move for **anything** patient-affecting: the clinic (or you) can switch the AI off instantly. Calls
then go to staff or voicemail.
`curl -X POST https://api…/api/clinic/ai -H "Authorization: Bearer <owner token>" -d '{"enabled": false}'`

## API down
1. Check the ALB target health and ECS service events (`aws ecs describe-services --cluster callbridge-production --services api`).
2. A failed deploy rolls back by itself (deployment circuit breaker). If not: redeploy the previous sha with `scripts/deploy.sh`.
3. While the API is down, Twilio can't fetch TwiML: set the **Twilio number's fallback URL** to a TwiML Bin with `<Dial>` to the clinic (configure it once per number, in advance).

## LLM provider down
The chain fails over automatically (Claude → GPT → take a message). Confirm in Grafana ("LLM calls by provider").
If both are down for more than 15 minutes, set `CALLBRIDGE_LLM_PROVIDER=rules` (offline parsing) and redeploy. It handles common requests.

## Twilio down
Nothing we can fix. Post on the status page. The clinic's own line still rings, because the forward happens only on no-answer.

## Deepgram / Cartesia down
Callers get Twilio's voicemail via `/twilio/after-stream`. Check `ErrorFrame` logs. If it lasts, switch the clinic's AI off (calls go to staff or voicemail).

## Slow replies
Grafana p95 panel. Check, in order: LLM latency (Langfuse), prompt cache hit rate (`cache_read_input_tokens`), Deepgram endpointing, task CPU (scale out), DB (Performance Insights).

## Cost spike
`DailyCostHigh` alert. The cap switches to offline parsing automatically at £20/day. Look for a call loop (one call with many turns) or abuse (many calls from one number). Block the number in Twilio.

## Safety blocks spike
Many checker rejections suggest an attack or a bad config release. Look at `audit_log` reasons. If a canary is running, roll it back: `callbridge improve rollback vN`.

## Red flag mishandled (any report)
Severity 1. Switch the AI off for that clinic, call the clinic, pull the transcript, write a postmortem, and add the utterance to `safety/redflags.py` plus a scenario. Ship only after the full gate passes.

## Restore test (do once per quarter, and before the first pilot)
1. `aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier callbridge-staging --target-db-instance-identifier callbridge-restore-test --use-latest-restorable-time`
2. Connect, count rows in `bookings`/`calls`, and run `callbridge eval --filter book_new` against it.
3. Record the time taken and the result in the numbers log, then delete the instance.

## Bad outcomes
Many `no_action` calls: run `callbridge improve run` and look at the new test cases and transcripts in the dashboard.
