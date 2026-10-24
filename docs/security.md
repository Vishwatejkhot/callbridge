# Security

## Threat model (what we defend against)

| Threat | Defence |
|---|---|
| Caller tricks the AI into acting for someone else | Verification (ADR 0002). The checker compares every proposal with the *system's* verified patient. 50+ adversarial checker tests plus 55 adversarial conversations in the eval suite. |
| Prompt injection ("ignore your rules…") | Screened before the model. The model's output is structured only; replies are templates; the model never holds other patients' data. |
| Reading out another patient's details | Nothing in the model context contains them. Replies only include the verified caller's own bookings. `other_patient` screen. |
| One clinic's staff seeing another clinic's data | JWT carries `clinic_id`; every query filters by it; Postgres RLS (`FORCE ROW LEVEL SECURITY`, non-owner app role) enforces it again. Tested in `tests/test_api.py` and `tests/test_postgres_rls.py`. |
| Forged Twilio webhooks | `X-Twilio-Signature` validation (refuses to start in production without the auth token). Recording callbacks carry `clinic_id` inside the signed URL. |
| Abuse / brute force | Per-IP rate limit; slow failed logins; PBKDF2-SHA256 (310k iterations) password hashes; max 3 changes per call; daily LLM cost cap. |
| Secrets leakage | All secrets in AWS Secrets Manager (KMS); none in images, tfvars or state; app refuses to start outside dev with the default JWT secret. |
| PII in logs and traces | `RedactingFilter` on every log handler; transcripts redacted before storage; Sentry `send_default_pii=False` + scrubber; Langfuse inputs redacted. |
| Data at rest / in transit | RDS + Secrets + logs encrypted with KMS (rotation on); `rds.force_ssl=1`; TLS 1.3 policy at the ALB; HTTP redirects to HTTPS. |
| Supply chain | Locked dependencies (`uv.lock`, `package-lock.json`); ECR scan-on-push; immutable image tags. |
| Bad AI change shipped | CI eval gate (any safety failure blocks merge); improvement loop needs gate → shadow → human → canary; auto-rollback. |

## Access
* Staff: one account per person, scoped to one clinic; `owner` role for reports, the kill switch and erasure.
* Engineers: AWS SSO with MFA; production database access only via a break-glass role (logged).
* `/metrics` is blocked at the load balancer; Prometheus scrapes inside the VPC.

## Known gaps / next
* Staff MFA on the dashboard (planned before the second pilot clinic).
* Move the per-IP rate limiter to the ALB/WAF when running more than one API task.
* Cyber Essentials certification (see compliance.md).
