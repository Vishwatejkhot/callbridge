# UK GDPR compliance

Bookings at a physio clinic imply health information, so we treat **all** patient data as special category
data (Art. 9). **The clinic is the controller. Callbridge is the processor.**

## Checklist

| Item | Status | Where |
|---|---|---|
| ICO registration (data protection fee) as processor | ☐ to do before the first pilot | ico.org.uk/fee |
| Data Processing Agreement with each clinic (Art. 28) | ☐ template to draft with an adviser | `docs/legal/` (private) |
| DPIA | ☐ draft from the ICO template; the inputs are below | ico.org.uk DPIA template |
| Privacy notice (website) + AI/recording notice at the start of every call | ✅ notice in code: `PromptConfig.greeting` | agent/prompts.py |
| UK data residency | ✅ everything in AWS eu-west-2 | infra/terraform |
| Encryption in transit and at rest | ✅ | security.md |
| Retention: recordings 30 days, transcripts 90 days, audit log 6 years | ✅ `callbridge purge` nightly | callbridge/jobs.py |
| Right to erasure ("delete my data") | ✅ by phone (verified) and by clinic owner via API | tools/booking.py: forget_patient |
| PII redaction in logs, traces, test cases | ✅ | safety/pii.py |
| Scope limited to admin; never clinical advice | ✅ enforced + tested (medical_advice evals) | safety/injection.py |
| Private clinics only (no NHS: DCB0129/DCB0160 out of scope) | ✅ policy | this file |
| Sub-processor review (below) | ☐ confirm terms per provider before go-live | |
| Cyber Essentials | ☐ optional, recommended | |
| Adviser review before real patient data flows | ☐ book with the university enterprise / legal team | |

## Sub-processors (check terms and region before go-live)

| Provider | Purpose | Data | Action before go-live |
|---|---|---|---|
| AWS (eu-west-2) | Hosting, database | all | DPA via AWS GDPR DPA |
| Twilio | Telephony, SMS, recordings | audio, numbers, SMS text | Use Twilio's IE1/UK data residency where available; sign DPA |
| Deepgram | Speech to text | audio | Confirm data retention opt-out / no-training terms |
| Cartesia | Text to speech | reply text (no caller audio) | Confirm retention terms |
| Anthropic | Understanding (Claude) | redacted-at-rest; live utterance text | Commercial terms (no training on API data); request zero data retention |
| OpenAI | Backup understanding | live utterance text | API data controls / ZDR |
| Groq | Easy-turn understanding (optional) | live utterance text | Confirm terms, or keep disabled (rules handle easy turns) |
| Sentry, Langfuse | Errors, traces | redacted only | EU region / self-host Langfuse in eu-west-2 |

## DPIA inputs
* **Processing:** answering calls a clinic would otherwise miss; booking admin; FAQs; messages.
* **Necessity:** missed calls mean lost patients; the clinic can switch the AI off at any time (`/api/clinic/ai`).
* **Risks:** wrong booking, disclosure to the wrong person, medical situation mishandled, provider breach.
* **Mitigations:** ADR 0001/0002, red-flag routing (100% escalation in evals), redaction, RLS, retention,
  audit log, human takeover, staff review of failed calls.
