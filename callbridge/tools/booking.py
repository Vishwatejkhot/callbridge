"""Booking tools. Read tools are free to call; write tools only run through `propose_and_execute`,
which goes via the safety checker first. There is deliberately no other write path."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.db.models import AuditLog, Booking, Call, Clinic, MemoryFact, Message, Patient, Practitioner, SmsLog
from callbridge.safety import checker
from callbridge.safety.proposals import (
    BookProposal, CallerContext, CancelProposal, Decision, ForgetProposal, Proposal, RescheduleProposal,
)
from callbridge.tools import availability, sms


@dataclass
class Slot:
    start: datetime
    practitioner_id: int
    practitioner_name: str

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "practitioner_id": self.practitioner_id, "practitioner_name": self.practitioner_name}

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        return cls(datetime.fromisoformat(d["start"]), d["practitioner_id"], d["practitioner_name"])


@dataclass
class ActionResult:
    ok: bool
    decision: Decision
    booking_id: int | None = None
    sms_sent: bool = False


def practitioners(s: Session, clinic_id: int, service: str | None = None) -> list[Practitioner]:
    q = select(Practitioner).where(Practitioner.clinic_id == clinic_id, Practitioner.active.is_(True))
    if service:
        q = q.where(Practitioner.service == service)
    return list(s.scalars(q.order_by(Practitioner.id)))


def check_slots(s: Session, clinic_id: int, *, day: date | None = None, practitioner_id: int | None = None,
                service: str | None = None, daypart: str | None = None, after_time: str | None = None,
                limit: int = 3, ignore_booking_id: int | None = None, search_days: int = 14,
                min_distinct: int = 1) -> list[Slot]:
    """Free slots on `day` (or the next days with availability), filtered by preferences."""
    clinic = s.get(Clinic, clinic_id)
    assert clinic is not None
    pracs = practitioners(s, clinic_id, service)
    if practitioner_id is not None:
        pracs = [p for p in pracs if p.id == practitioner_id]
    days = [day] if day else [clock.now().date() + timedelta(days=i) for i in range(search_days)]
    out: list[Slot] = []
    for d in days:
        for start, p in availability.free_slots(s, clinic, pracs, d, ignore_booking_id):
            if daypart == "morning" and start.hour >= 12:
                continue
            if daypart == "afternoon" and not (12 <= start.hour < 17):
                continue
            if daypart == "evening" and start.hour < 17:
                continue
            if after_time and start.strftime("%H:%M") < after_time:
                continue
            out.append(Slot(start, p.id, p.name))
        if day is None and len({sl.start for sl in out}) >= min_distinct:
            break  # enough distinct times to offer a real choice
    return _spread(out, limit)


def _spread(slots: list[Slot], limit: int) -> list[Slot]:
    """Offer choices spread across the day rather than three back-to-back times."""
    if len(slots) <= limit:
        return slots
    distinct_times: list[Slot] = []
    seen: set[datetime] = set()
    for sl in slots:
        if sl.start not in seen:
            seen.add(sl.start)
            distinct_times.append(sl)
    pool = distinct_times if len(distinct_times) >= limit else slots
    step = len(pool) / limit
    return [pool[int(i * step)] for i in range(limit)]


def find_slot(s: Session, clinic_id: int, start: datetime, practitioner_id: int | None = None,
              service: str | None = None, ignore_booking_id: int | None = None) -> Slot | None:
    for sl in check_slots(s, clinic_id, day=start.date(), practitioner_id=practitioner_id, service=service,
                          limit=1000, ignore_booking_id=ignore_booking_id):
        if sl.start == start:
            return sl
    return None


def upcoming_bookings(s: Session, clinic_id: int, patient_id: int) -> list[Booking]:
    return list(s.scalars(select(Booking).where(
        Booking.clinic_id == clinic_id, Booking.patient_id == patient_id, Booking.status == "booked",
        Booking.start >= clock.now()).order_by(Booking.start)))


def propose_and_execute(s: Session, ctx: CallerContext, proposal: Proposal) -> ActionResult:
    """The single write path: check -> audit -> execute -> mark executed -> confirm by SMS."""
    decision, audit = checker.check_and_record(s, ctx, proposal)
    if not decision.allowed:
        return ActionResult(ok=False, decision=decision)

    clinic = s.get(Clinic, ctx.clinic_id)
    assert clinic is not None
    length = timedelta(minutes=clinic.appointment_minutes)
    booking_id: int | None = None
    sms_text: str | None = None

    if isinstance(proposal, BookProposal):
        b = Booking(clinic_id=clinic.id, patient_id=proposal.patient_id, practitioner_id=proposal.practitioner_id,
                    start=proposal.start, end=proposal.start + length, created_by="ai", call_id=ctx.call_id)
        s.add(b)
        s.flush()
        booking_id = b.id
        sms_text = f"{clinic.name}: you're booked with {_pname(s, b.practitioner_id)} {speak_on(b.start)}. Reply or call us to change."
    elif isinstance(proposal, RescheduleProposal):
        b = s.get(Booking, proposal.booking_id)
        assert b is not None
        b.start, b.end, b.practitioner_id = proposal.start, proposal.start + length, proposal.practitioner_id
        booking_id = b.id
        sms_text = f"{clinic.name}: your appointment has moved to {speak_datetime(b.start)} with {_pname(s, b.practitioner_id)}."
    elif isinstance(proposal, CancelProposal):
        b = s.get(Booking, proposal.booking_id)
        assert b is not None
        b.status = "cancelled"
        booking_id = b.id
        sms_text = f"{clinic.name}: your appointment {speak_on(b.start)} has been cancelled."
    elif isinstance(proposal, ForgetProposal):
        forget_patient(s, clinic.id, proposal.patient_id)

    audit.executed = True
    s.flush()
    sent = False
    if sms_text and ctx.caller_phone:
        sent = sms.send(s, clinic.id, ctx.caller_phone, sms_text)
    return ActionResult(ok=True, decision=decision, booking_id=booking_id, sms_sent=sent)


def forget_patient(s: Session, clinic_id: int, patient_id: int) -> None:
    """Right to erasure: database rows, memory, transcripts, messages, SMS log, recordings.
    Audit entries are kept but stripped of the patient reference (legal obligation to evidence actions)."""
    patient = s.get(Patient, patient_id)
    if patient is None or patient.clinic_id != clinic_id:
        return
    phone = patient.phone
    s.execute(delete(MemoryFact).where(MemoryFact.clinic_id == clinic_id, MemoryFact.patient_id == patient_id))
    s.execute(delete(Booking).where(Booking.clinic_id == clinic_id, Booking.patient_id == patient_id))
    calls = list(s.scalars(select(Call).where(Call.clinic_id == clinic_id, Call.patient_id == patient_id)))
    if phone:
        calls += list(s.scalars(select(Call).where(Call.clinic_id == clinic_id, Call.caller_phone == phone)))
        s.execute(delete(Message).where(Message.clinic_id == clinic_id, Message.caller_phone == phone))
        s.execute(delete(SmsLog).where(SmsLog.clinic_id == clinic_id, SmsLog.to == phone))
    from callbridge.voice.recordings import delete_recording

    for c in {c.id: c for c in calls}.values():
        if c.recording_url:
            delete_recording(c.recording_url)
        c.transcript, c.summary, c.caller_phone, c.patient_id, c.recording_url = [], "[erased]", None, None, None
    for a in s.scalars(select(AuditLog).where(AuditLog.clinic_id == clinic_id)):
        if a.proposal.get("patient_id") == patient_id:
            a.proposal = {**a.proposal, "patient_id": None}
    s.delete(patient)
    s.flush()


def _pname(s: Session, practitioner_id: int) -> str:
    p = s.get(Practitioner, practitioner_id)
    return p.name.split()[0] if p else "your practitioner"


def speak_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{h}{suffix}" if dt.minute == 0 else f"{h}:{dt.minute:02d}{suffix}"


def speak_date(d: date) -> str:
    today = clock.now().date()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    day = d.day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{d.strftime('%A')} the {day}{suffix} of {d.strftime('%B')}"


def speak_datetime(dt: datetime) -> str:
    return f"{speak_date(dt.date())} at {speak_time(dt)}"


def speak_on(dt: datetime) -> str:
    """'on Thursday the 29th of October at 3pm', but 'tomorrow at 3pm' (no 'on')."""
    text = speak_datetime(dt)
    return text if text.startswith(("today", "tomorrow")) else f"on {text}"
