""""AI proposes, code decides." Plain, deterministic rules. No LLM anywhere in this file.

Every proposal is checked against what the *system* knows about the caller (CallerContext), never
against anything the model says. Every verdict is written to the audit log, allowed or not."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.db.models import AuditLog, Booking, Clinic, Patient, Practitioner
from callbridge.safety.proposals import (
    BookProposal, CallerContext, CancelProposal, Decision, ForgetProposal, Proposal, RescheduleProposal,
)
from callbridge.tools import availability

MAX_MUTATIONS_PER_CALL = 3


def check(s: Session, ctx: CallerContext, p: Proposal) -> Decision:
    reasons: list[str] = []
    clinic = s.get(Clinic, ctx.clinic_id)
    if clinic is None or not clinic.ai_enabled:
        return Decision(allowed=False, reasons=["clinic_unavailable"])
    if ctx.verified_patient_id is None:
        return Decision(allowed=False, reasons=["caller_not_verified"])

    executed = s.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.call_id == ctx.call_id, AuditLog.executed.is_(True)))
    if executed >= MAX_MUTATIONS_PER_CALL:
        reasons.append("too_many_changes_in_one_call")
    if _already_executed(s, ctx, p):
        reasons.append("duplicate_action")

    if isinstance(p, BookProposal):
        if p.patient_id != ctx.verified_patient_id:
            reasons.append("patient_is_not_caller")
        patient = s.get(Patient, p.patient_id)
        if patient is None or patient.clinic_id != clinic.id:
            reasons.append("patient_not_found")
        reasons += _slot_problems(s, clinic, p.practitioner_id, p.start, ignore_booking_id=None)
        clash = s.scalar(select(Booking).where(
            Booking.clinic_id == clinic.id, Booking.patient_id == p.patient_id, Booking.status == "booked",
            Booking.start < p.start + timedelta(minutes=clinic.appointment_minutes),
            Booking.end > p.start))
        if clash is not None:
            reasons.append("patient_already_booked_at_this_time")

    elif isinstance(p, RescheduleProposal):
        if not clinic.allow_reschedule:
            reasons.append("reschedule_not_allowed_for_clinic")
        b = _owned_future_booking(s, ctx, clinic, p.booking_id, reasons)
        if b is not None and b.start == p.start and b.practitioner_id == p.practitioner_id:
            reasons.append("no_change")
        reasons += _slot_problems(s, clinic, p.practitioner_id, p.start, ignore_booking_id=p.booking_id)

    elif isinstance(p, CancelProposal):
        if not clinic.allow_cancel:
            reasons.append("cancel_not_allowed_for_clinic")
        _owned_future_booking(s, ctx, clinic, p.booking_id, reasons)

    elif isinstance(p, ForgetProposal):
        if p.patient_id != ctx.verified_patient_id:
            reasons.append("patient_is_not_caller")
        patient = s.get(Patient, p.patient_id)
        if patient is None or patient.clinic_id != clinic.id:
            reasons.append("patient_not_found")

    else:  # pragma: no cover - the Proposal union is closed
        reasons.append("unknown_action")

    return Decision(allowed=not reasons, reasons=reasons)


def record(s: Session, ctx: CallerContext, p: Proposal, decision: Decision, executed: bool) -> AuditLog:
    """Write the audit row once, in its final state. The app role can't UPDATE or DELETE audit rows
    (infra/sql/001_rls.sql); it's written in the same transaction as the action, so they commit together."""
    entry = AuditLog(
        clinic_id=ctx.clinic_id, call_id=ctx.call_id, action=p.action, proposal=p.model_dump(mode="json"),
        allowed=decision.allowed, reasons=decision.reasons, executed=executed,
    )
    s.add(entry)
    s.flush()
    return entry


def _slot_problems(s: Session, clinic: Clinic, practitioner_id: int, start, ignore_booking_id: int | None) -> list[str]:
    out: list[str] = []
    prac = s.get(Practitioner, practitioner_id)
    if prac is None or prac.clinic_id != clinic.id or not prac.active:
        return ["practitioner_not_at_clinic"]
    if not availability.on_grid(clinic, start):
        out.append("not_a_valid_slot_time")
    if not availability.within_opening_hours(s, clinic, start):
        out.append("outside_opening_hours")
    if start < availability.earliest_allowed(clinic):
        out.append("too_soon_or_in_past")
    if start > clock.now() + timedelta(days=clinic.max_days_ahead):
        out.append("too_far_ahead")
    end = start + timedelta(minutes=clinic.appointment_minutes)
    if availability.overlapping(s, clinic.id, practitioner_id, start, end, ignore_booking_id):
        out.append("slot_taken")
    return out


def _owned_future_booking(s: Session, ctx: CallerContext, clinic: Clinic, booking_id: int, reasons: list[str]) -> Booking | None:
    b = s.get(Booking, booking_id)
    if b is None or b.clinic_id != clinic.id:
        reasons.append("booking_not_found")
        return None
    if b.patient_id != ctx.verified_patient_id:
        reasons.append("booking_belongs_to_someone_else")
    if b.status != "booked":
        reasons.append("booking_not_active")
    if b.start < availability.earliest_allowed(clinic):
        reasons.append("booking_too_soon_to_change")
    return b


def _already_executed(s: Session, ctx: CallerContext, p: Proposal) -> bool:
    payload = p.model_dump(mode="json")
    for entry in s.scalars(select(AuditLog).where(AuditLog.call_id == ctx.call_id, AuditLog.executed.is_(True))):
        if entry.proposal == payload:
            return True
    return False
