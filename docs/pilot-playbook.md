# Pilot playbook (plan section 0 and 7)

Code can't do this part. It starts in week 1, in parallel with the build.

## Outreach
- [ ] List 30 small private clinics in Bristol (physio, osteo, chiro, dental) in a spreadsheet: name, number, owner, notes.
- [ ] Phone each at lunchtime (12:30–13:30) and note whether anyone answered. This is your evidence.
- [ ] Email or visit 10–15 with the offer: *free for 3 months, only answers calls you'd miss, bookings and basic questions only, never medical advice, switch it off any time.*
- [ ] Ask to sit at the front desk for an hour. Write down the real questions patients ask and add them as scenarios.
- [ ] Target: 2–3 pilot clinics by week 6. Physio and osteo first.

## Onboarding a clinic (about 1 hour)
1. Sign the DPA. Add the clinic to the sub-processor list shared with them.
2. Create the clinic row, practitioners, hours and knowledge chunks (their real FAQs), and staff accounts.
3. Buy a UK Twilio number. Set the voice webhook to `https://api…/twilio/voice` and the fallback URL to a TwiML Bin dialling the clinic.
4. The clinic sets **forward on no answer / busy / out of hours** to that number. Their phone still rings first.
5. Run 20 test calls together on their number (fictional patients only). Show them the dashboard and the kill switch.
6. Start out-of-hours only. Widen after two clean weeks.

## Weekly
- [ ] 30-minute check-in per clinic: what went wrong, what they want.
- [ ] Review failed calls in the dashboard and run `callbridge improve run`.
- [ ] Update the numbers log in docs/slos.md.
- [ ] Ask happy clinics for a one-line quote.

**If no clinic has said yes by week 8:** keep the public demo line running, keep reaching out, and say
"pilot-ready" (not "in production") until a clinic is live.
