# Callbridge – Production Build Plan

**What it is:** An AI phone receptionist for private clinics (physio, dental, osteopath, chiropractor, private GP). A patient calls, the AI answers, books / changes / cancels appointments, answers simple questions, and hands the call to a human when needed.

**What makes it special:** It is a **self-improving, safety-verified voice agent**.
- Every failed call becomes a test.
- Every fix is proven before it goes live.
- The AI can never make a wrong booking, because plain code checks every action.

**Goal:** Real clinics using it on real phone lines, so your CV says *production*, not *demo*.

**Time:** 8 weeks to build + at least 6 weeks running with real users (do both at the same time from week 6).

---

## A. What is Callbridge?

### The problem
Small private clinics (physio, dental, osteopath) miss a lot of phone calls:
- The receptionist is busy with a patient at the desk
- The practitioner is in a treatment session and can't answer
- The clinic is closed (evenings, weekends, lunch)

A missed call often means a **lost patient**. The caller just phones another clinic. Hiring a full-time receptionist is expensive for a small clinic.

### The solution
**Callbridge is an AI receptionist that answers the clinic's phone when staff can't.**

It can:
- Book, move and cancel appointments
- Answer simple questions (opening hours, prices, parking, address)
- Remember returning patients
- Send SMS confirmations
- Take a message for staff to call back
- Pass urgent or difficult calls to a human straight away

It **cannot and will not**:
- Give medical advice
- Tell a patient what's wrong with them
- Share one patient's details with another person

### Who uses it
| Person | How they use it |
|---|---|
| **Patient** | Just phones the clinic as normal and talks |
| **Clinic staff** | See calls live on a dashboard, take over any call, see messages and new bookings |
| **Clinic owner** | Sees reports: calls answered, bookings made, time saved |
| **You (the engineer)** | Monitor, fix, improve and keep it running |

### What makes it different from other AI receptionists
Many companies sell AI phone agents. Callbridge is different in three ways:

1. **It improves itself, safely.** When a call goes wrong, the system turns it into a test, suggests a fix, and only ships the fix after it passes every old test. Most AI agents get worse over time; this one gets better.
2. **The AI can't make wrong bookings.** The AI only *suggests* actions. Plain code checks every action before it happens. Even if someone tricks the AI, the booking won't go through.
3. **Humans stay in control.** Staff can see every call live and take over with one click, with a summary ready so the patient never repeats themselves.

---

## B. How it works – one call, step by step

Example: Sarah calls her physio clinic at 7pm (clinic closed) to move her appointment.

**Step 1 – The call arrives**
The clinic's phone number forwards to Callbridge when nobody answers or after hours. Twilio receives the call and streams the audio to the Callbridge voice server.

**Step 2 – The AI greets her**
> "Hi, you've reached Bristol Physio. I'm an AI assistant, and this call may be recorded. How can I help?"

The greeting is quick because it's prepared in advance.

**Step 3 – Listening**
Sarah says: *"Hi, I need to move my appointment on Tuesday."*
- Deepgram turns her voice into text **while she is still speaking** (streaming)
- Smart turn detection works out when she has *finished her thought*, not just paused
- The AI starts preparing a reply early to save time

**Step 4 – Recognising her**
Callbridge sees her phone number and finds her in memory:
*Sarah, usual physio is Tom, last visit 2 weeks ago.*
For safety, it still checks her identity before changing anything:
> "Can I just confirm your date of birth?"

**Step 5 – Deciding what to do**
The brain (LangGraph) follows clear steps: *understand → verify → find options → confirm → act.*
- Easy turns ("yes", "thanks") → small fast model (cheap, quick)
- Harder turns (understanding the request) → Claude
- It calls the `check_slots` tool and finds Thursday at 3pm and 5pm with Tom.

> "Tom has Thursday at 3pm or 5pm. Which works better?"

**Step 6 – The safety check (key feature)**
Sarah picks 5pm. The AI **does not** change the booking itself. It sends a *proposal*:
> `reschedule booking #42 → Thursday 5pm, practitioner Tom`

The **safety checker** (plain Python code, no AI) checks:
- ✅ Is the caller verified?
- ✅ Does booking #42 belong to Sarah?
- ✅ Is Thursday 5pm really free?
- ✅ Is it within opening hours?
- ✅ Is rescheduling allowed for this clinic?

All pass → the booking is changed. Everything is written to the audit log.

**Step 7 – Confirming**
> "Done. You're now booked with Tom on Thursday at 5pm. I've sent you a text. Anything else?"

An SMS confirmation arrives on Sarah's phone.

**Step 8 – If something goes wrong**
- Sarah says *"Actually I've had chest pain today"* → red-flag detected → *"If it's severe or getting worse, please call 999 now. Otherwise, NHS 111 can advise you."* Then the call is flagged for staff.
- The AI gets confused or Sarah gets frustrated → confidence drops → staff get an alert. If nobody is available, the AI takes a message for a call-back.
- Claude is down → automatic switch to GPT. If speech services fail → *"Sorry, let me take a message."* The line never goes silent.

**Step 9 – After the call**
- The call is traced in Langfuse (every step, delay and cost)
- Personal details are hidden in logs
- Metrics go to Grafana (delay, cost, success)
- If the call failed, it goes into the **improvement loop** (Step 10)

**Step 10 – The system gets better (key feature)**
Say many callers say *"next Tuesday"* and the AI picks the wrong Tuesday.
1. Those failed calls are spotted automatically
2. They become new test cases (with personal data removed)
3. The improver agent suggests a fix to the prompt or flow
4. The fix is tested against **all** old tests. It must fix the new problem and break nothing.
5. You approve it
6. It runs in **shadow mode** (silently, alongside the live version), then on **10%** of calls, then **100%**
7. If numbers drop at any point → **automatic rollback**

---

## C. Why this is production level (not just a demo)

A demo works once, on your laptop, when you test it. **Production means real people depend on it every day, and it keeps working when things go wrong.** Here is how Callbridge meets that bar:

| Production requirement | What a demo has | What Callbridge has |
|---|---|---|
| **Real users** | Only you test it | Real clinics, real patients, real calls |
| **Reliability** | Breaks if one service fails | Fallback LLMs, retries, circuit breakers, never-silent line, 99.5% uptime target |
| **Speed** | "Feels OK" | Measured p95 delay with a target (<800 ms), before/after improvements |
| **Scale** | One call at a time | Load-tested with 50–100 calls at the same time |
| **Safety** | Trusts the AI | "AI proposes, code decides": every action checked by plain code |
| **Security** | Secrets in code, one user | Secrets manager, per-clinic data isolation (row-level security), login, rate limits, injection defence |
| **Privacy & law** | Ignored | UK GDPR: ICO registration, DPIA, DPAs, UK hosting, data deletion, PII redaction |
| **Testing** | Manual testing | 300+ automated fake-patient tests that run on every code change |
| **Releases** | Push and hope | CI gate, shadow → canary → full rollout, auto-rollback |
| **Monitoring** | Print statements | Langfuse traces, Grafana dashboards, Sentry errors, phone alerts, status page |
| **Infrastructure** | Runs on laptop | Terraform on AWS London, staging + production, one-command deploy, tested backups |
| **Operations** | No plan | Incident runbook, postmortems, cost caps, weekly reviews with clinics |
| **Improvement** | Stays the same | Self-improving loop from real failures |
| **Humans in the loop** | None | Live staff dashboard with one-click takeover |

### Why a hiring manager will see it as senior work
A junior engineer builds an AI that **works**.
A senior engineer builds an AI that **keeps working, safely, for real users, and gets better over time**, and can prove it with numbers.

Callbridge shows you can:
- Design a full system, not just call an LLM
- Think about what goes wrong (failures, attacks, bad answers, outages)
- Handle sensitive data legally and safely
- Measure quality and stop bad releases automatically
- Run something real, deal with real incidents, and learn from them
- Work with real customers (the clinics)

That is exactly what startups need from an early or senior AI engineer.

---

## 0. Start this now (in parallel with building)

Clinics take weeks to say yes. Start talking to them **in week 1**, not week 8.

- [ ] Make a list of 30 small private clinics in Bristol (physio, dental, osteo, chiro)
- [ ] Phone them at lunchtime and count how often nobody answers. This is your proof they need it
- [ ] Visit or email 10–15 of them with a simple offer:
  > "Free for 3 months. It only answers the calls you'd miss anyway: out-of-hours and when you're busy. It only handles bookings and basic questions, never medical advice. You can switch it off anytime."
- [ ] Aim for **2–3 pilot clinics** by week 6
- [ ] Physio and osteopath clinics are usually the easiest: small, owner-run, often no full-time receptionist

**Tip:** Ask "Can I sit at your front desk for one hour and see how calls work?" You'll learn what patients really ask, and the owner will trust you more.

---

## 1. What the finished system does

1. Patient calls the clinic's number (forwarded to Callbridge when staff can't answer)
2. AI says: *"Hi, you've reached [Clinic]. I'm an AI assistant. This call may be recorded. How can I help?"*
3. Patient talks normally: *"Can I move my physio appointment to Thursday?"*
4. AI checks who they are, finds free slots, and **proposes** the change
5. A safety checker (plain code, not AI) confirms the action is valid → booking is made
6. AI reads it back to confirm and sends an SMS confirmation
7. Returning patients are remembered: *"Hi Sarah, same physio as last time?"*
8. Anything medical, urgent or confusing → passed to a human or the right service
9. Staff see live calls on a dashboard and can take over with one click
10. Failed calls are automatically turned into tests so the system gets better

---

## 2. Tools

| Job | Tool |
|---|---|
| Phone number + SMS | **Twilio** (UK number) |
| Voice pipeline | **Pipecat** (or LiveKit Agents) |
| Speech to text | **Deepgram** |
| Text to speech | **Cartesia** or **ElevenLabs** |
| LLMs | **Claude** (main), **GPT** (backup), **Groq** small model (fast/easy turns) |
| Conversation flow | **LangGraph** |
| Booking tools | **MCP server** (Python) |
| Safety checker | Plain Python rules (+ optional **OPA** policy engine) |
| Backend | **FastAPI** |
| Database | **PostgreSQL** + **pgvector** (AWS RDS) |
| Staff dashboard | **Next.js** |
| Tracing | **Langfuse** |
| Metrics + charts | **Prometheus** + **Grafana** |
| Errors | **Sentry** |
| Uptime alerts + status page | **Better Stack** |
| Load testing | **k6** or **Locust** |
| Infrastructure | **Docker** + **Terraform** on **AWS London region (eu-west-2)** |
| CI/CD | **GitHub Actions** |
| Secrets | **AWS Secrets Manager** |

Check current versions and prices before you start. These change often.

---

## 3. How it fits together

```
Patient's phone
      │
      ▼
   Twilio (clinic number forwards here)
      │
      ▼
   Voice server (Pipecat)
      ├── Speech → text  (Deepgram)
      ├── Smart turn detection (knows when caller has finished)
      │
      ├── Brain (LangGraph)
      │     ├── Model router: easy turn → small fast model
      │     │                 hard turn → Claude (backup: GPT)
      │     ├── Memory (past callers)
      │     ├── Clinic info search (RAG)
      │     └── Proposes actions ─────────┐
      │                                    ▼
      │                         SAFETY CHECKER (plain code)
      │                         "Is this allowed and correct?"
      │                                    │ yes
      │                                    ▼
      │                         MCP booking tools → Database
      │
      └── Text → speech (Cartesia)

   Staff dashboard  ← live calls, confidence, one-click takeover
   Improvement loop ← failed calls → tests → proposed fix → safe rollout
   Monitoring       ← Langfuse, Grafana, Sentry, uptime alerts
```

---

## 4. Folder layout

```
callbridge/
├── voice/          # Phone + audio pipeline
├── agent/          # LangGraph flow + model router
├── safety/         # "AI proposes, code decides" checker
├── tools/          # MCP server: slots, book, reschedule, cancel
├── memory/         # Remember past callers
├── knowledge/      # Clinic info (RAG)
├── improver/       # Self-improving loop
├── dashboard/      # Next.js staff dashboard
├── api/            # FastAPI
├── evals/          # Fake-patient test suite
├── loadtest/       # k6 / Locust
├── infra/          # Terraform + Docker
├── docs/           # Architecture, decisions, security, postmortems, runbook
└── README.md
```

---

## 5. Week-by-week build

### Week 1 – Phone answers and talks

- [ ] Twilio UK number → Pipecat → Deepgram + LLM + Cartesia
- [ ] Caller can interrupt the AI (barge-in)
- [ ] AI notice at the start of every call
- [ ] Measure reply delay (caller stops → AI starts). **Write the number down.**
- [ ] Start clinic outreach (section 0)

**Done when:** You can phone it and have a natural short chat.

---

### Week 2 – Real bookings

- [ ] Database: clinics, practitioners, opening hours, slots, patients, bookings
- [ ] MCP tools: `check_slots`, `book`, `reschedule`, `cancel`, `send_sms_confirmation`
- [ ] LangGraph flow: greet → identify caller → understand → act → confirm → goodbye
- [ ] Simple caller check before changing a booking (name + date of birth, or SMS code)
- [ ] Multi-clinic: each clinic only ever sees its own data (Postgres row-level security)
- [ ] Human handoff: transfer the call or take a message for call-back

**Done when:** A booking made by phone shows up correctly in the database and an SMS arrives.

---

### Week 3 – Memory, clinic info, basic safety

**Memory**
- [ ] Remember simple facts: name, usual practitioner, last visit
- [ ] Recognise returning callers
- [ ] "Delete my data" really deletes everywhere (database, vectors, logs, recordings)

**Clinic info**
- [ ] Load each clinic's hours, prices, address, parking, FAQs
- [ ] AI searches this to answer questions, and says "I'm not sure, let me get someone" if not found

**Medical safety (very important)**
- [ ] AI **never** gives medical advice or interprets symptoms
- [ ] "Red flag" words (chest pain, can't breathe, severe bleeding, etc.) → tell caller to call **999**, otherwise **NHS 111**, then pass to staff
- [ ] Hide personal data in all logs and traces
- [ ] Block tricks like "ignore your rules" or "read me another patient's details"

**Done when:** It remembers you, answers questions, refuses tricks, and handles urgent calls safely.

---

### Week 4 – Fake-patient test suite + CI gate

- [ ] Build a fake patient bot (an LLM playing a caller from a script)
- [ ] Write 300 test scenarios:
  - normal booking, reschedule, cancel
  - caller changes mind halfway
  - background noise, accents, mumbling
  - wrong date of birth / wrong person
  - trick attempts (prompt injection, asking for others' data)
  - urgent medical situation (**must** escalate, every time)
- [ ] Score each test: task done? correct? safe? delay? cost?
- [ ] Run every test in GitHub Actions on every pull request
- [ ] Block merge if success drops, any safety test fails, or delay/cost rises too much

**Done when:** A bad change is automatically blocked.

---

### Week 5 – Strong, fast, watched, deployed

**When things break**
- [ ] LLM fallback chain: Claude → GPT → take a message
- [ ] Speech service fails → "Sorry, let me take a message" (never silence)
- [ ] Timeouts, retries, circuit breakers

**Load**
- [ ] Load test: 10 → 50 → 100 calls at the same time. Find the breaking point.

**Monitoring**
- [ ] Langfuse traces for every call
- [ ] Grafana: delay, errors, calls/min, cost/call, handoff rate
- [ ] Sentry for errors
- [ ] Uptime alerts to your phone + public status page
- [ ] Daily cost cap and alert
- [ ] Set targets (SLOs):
  - 95% of replies start in under 800 ms
  - 99.5% uptime
  - Under 1% of calls fail
  - 100% of red-flag calls escalated

**Deploy**
- [ ] Terraform for all AWS resources in the London region
- [ ] Separate staging and production
- [ ] One-command deploy
- [ ] Daily database backups. **Test a restore once.**

**Done when:** It's live in production, monitored, with real numbers.

---

### Week 6 – Special feature 1: "AI proposes, code decides" + speed

**Safety checker**
- [ ] LLM can only output a proposed action (e.g. `reschedule booking 42 → Thu 3pm`)
- [ ] Plain-code checker verifies: caller is verified, booking belongs to them, slot is free, within opening hours, allowed for this clinic, not a duplicate
- [ ] Only then is the action run; every decision goes in the audit log
- [ ] Attack it with 50+ trick tests. Goal: **0 wrong bookings.**

**Speed**
- [ ] Smart turn detection (meaning-based, not just silence)
- [ ] Start drafting the reply while the caller is still talking; discard if they change direction
- [ ] Model router: easy turns → small fast model; hard turns → Claude
- [ ] Record before/after delay and cost

**Start pilots:** switch on for your first clinic (out-of-hours only).

---

### Week 7 – Special feature 2: Self-improving loop

- [ ] Detect failed calls automatically: hang-up mid-task, failed booking, handoff, low confidence, staff correction
- [ ] Turn each failed call into a new test case (with personal data removed)
- [ ] "Improver" agent reads recent failures and proposes a fix (prompt, flow step or tool description)
- [ ] Fix must pass the new test **and** all old tests
- [ ] Safe rollout:
  1. **Shadow:** new version runs silently next to the live one on real calls, outputs compared
  2. **Canary:** 10% of calls
  3. **Full:** 100%
  4. **Auto-rollback** if numbers drop
- [ ] A human (you) approves before anything goes to canary

**Done when:** You can show one real failure that the system turned into a test and fixed safely.

---

### Week 8 – Special feature 3: Staff dashboard + launch

**Staff dashboard**
- [ ] Live calls with running summary and AI confidence
- [ ] Alert when confidence drops
- [ ] One-click takeover, staff see the full summary so the patient doesn't repeat themselves
- [ ] Call history, messages to call back, bookings made by AI
- [ ] Login per clinic, staff only see their clinic

**Launch**
- [ ] Live demo number with a **fictional** clinic (fake data only)
- [ ] README: what it is, demo number, 1-min video, diagram, real numbers, quickstart
- [ ] Open-source the core (keep pilot clinics' data and config private)
- [ ] Post on LinkedIn: the self-improving loop is your headline

---

## 6. Compliance (clinic data is sensitive. Do this properly.)

Health-related data is **special category data** under UK GDPR. Even a booking for "physio" can count.

- [ ] Register with the **ICO** as a data processor (small fee)
- [ ] **Data Processing Agreement (DPA)** with each clinic: they are the controller, you are the processor
- [ ] **DPIA** (Data Protection Impact Assessment) document. Templates are on the ICO website
- [ ] Privacy notice on your website + AI/recording notice at start of every call
- [ ] All data stored in the **UK (AWS London)**
- [ ] Clear retention rules (e.g. recordings deleted after 30 days)
- [ ] Encryption in transit and at rest
- [ ] Check every provider (Twilio, Deepgram, LLMs) for data-processing terms and where data goes; prefer zero-retention options
- [ ] Keep scope admin-only: **bookings and FAQs, never clinical advice**
- [ ] Stay with **private** clinics. NHS work requires extra clinical safety standards (DCB0129 / DCB0160)
- [ ] Optional but strong: **Cyber Essentials** certification. Clinics trust it, and it looks great on a CV

Write it all in `docs/security.md` and `docs/compliance.md`. This alone shows senior thinking.

**Get advice:** have a quick check from a data protection adviser or your university's legal/enterprise team before real patient data flows. Many universities offer free startup support.

---

## 7. Running it in production (weeks 6–14+)

This is what turns "built" into "production".

- [ ] Phone alerts for downtime and errors. Respond to them
- [ ] Weekly check-in with each pilot clinic: what went wrong, what they want
- [ ] Weekly review of failed calls (feeds the improvement loop)
- [ ] Incident runbook in `docs/runbook.md` ("Twilio down → do X")
- [ ] Write a **postmortem** for every real incident in `docs/postmortems/`
- [ ] Track numbers weekly in a simple spreadsheet
- [ ] Ask happy clinics for a short quote you can use

**If no clinic says yes by week 8:** keep the live demo line running publicly, keep reaching out, and on your CV say "pilot-ready" until a real clinic is live. Never claim production use you don't have.

---

## 8. Numbers to collect (real ones only)

| Number | Example |
|---|---|
| Real clinics live | 3 |
| Real calls handled | 2,300 over 6 weeks |
| Calls fully handled without staff | 68% |
| Uptime | 99.7% |
| Reply delay p95 (before → after) | 1,400 ms → 620 ms |
| Cost per minute (before → after routing) | £0.09 → £0.05 |
| Concurrent calls tested | 80 |
| Test suite size | 300 + 140 auto-generated from real failures |
| Wrong bookings in adversarial tests | 0 of 50 |
| Red-flag calls escalated | 100% |
| Fixes shipped by improvement loop | 12 |
| Incidents + postmortems | 2 |

---

## 9. Final CV entry (fill in real numbers)

> **Callbridge – Self-improving production voice agent for private clinics**
> - Live with ___ UK clinics, handling ___ real patient calls over ___ weeks at ___% uptime; ___% resolved without staff.
> - Self-improving loop: failed calls auto-convert into regression tests; an improver agent proposes fixes that ship only after passing ___ scenarios via shadow and canary rollout with auto-rollback (___ fixes shipped).
> - "AI proposes, code decides" policy layer verifies every action: 0 invalid bookings across ___ adversarial tests; 100% red-flag escalation.
> - Smart turn detection, early reply drafting and model routing cut p95 latency ___ → ___ ms and cost ___%; load-tested to ___ concurrent calls.
> - UK GDPR compliant (DPIA, DPAs, UK hosting, PII redaction); Terraform on AWS, CI eval gates, Langfuse/Grafana/Sentry monitoring.

---

## 10. Simple rules

1. **Real users beat fancy features.** Start clinic outreach in week 1.
2. **Make it work, then make it special.** Weeks 1–5 must be solid before 6–8.
3. **Measure everything.** No number = didn't happen.
4. **Safety first.** One bad medical moment can end a pilot. Escalate when unsure.
5. **Never use real patient data in the demo or tests.**
6. **Write things down** as you go (decisions, incidents). They become interview stories.
7. **Only put true numbers on your CV.** You will be asked about them.
