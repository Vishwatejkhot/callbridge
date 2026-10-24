"""Adversarial tests for "AI proposes, code decides". Each case is something a tricked or confused
model might propose. Goal: 0 wrong bookings. Demo data at NOW (Mon 19 Oct 2026 09:00):
  Sarah (patient 1): booking 1 (past), booking 2 = Tue 20 Oct 10:00 with Tom
  David (patient 2): booking 3 = Wed 21 Oct 14:00 with Ellen, booking 4 (past)
  Practitioners: Tom 1, Priya 2, Ellen 3 (osteo), Jake 4. Hours Mon-Fri 8-19, Sat 9-13."""

from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from callbridge import db
from callbridge.db.models import AuditLog, Booking, Clinic, Patient, Practitioner
from callbridge.safety import checker
from callbridge.safety.proposals import (
    BookProposal, CallerContext, CancelProposal, ForgetProposal, RescheduleProposal,
)
from callbridge.tools.booking import propose_and_execute

SARAH, DAVID = 1, 2
TOM, PRIYA, ELLEN = 1, 2, 3
SARAH_NEXT, DAVID_NEXT, SARAH_PAST = 2, 3, 1


def at(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, d, h, m)


def ctx(verified=SARAH, call="c1") -> CallerContext:
    return CallerContext(call_id=call, clinic_id=1, caller_phone="+447700900123", verified_patient_id=verified)


@pytest.fixture
def other_clinic(clinic):
    with db.session() as s:
        c = Clinic(name="Elsewhere Osteo", slug="elsewhere", phone_number="+441170000001")
        s.add(c)
        s.flush()
        p = Practitioner(clinic_id=c.id, name="Zed Other", service="osteopathy")
        pat = Patient(clinic_id=c.id, first_name="Ola", last_name="Other", dob=date(1990, 1, 1), phone="+447700900777")
        s.add_all([p, pat])
        s.flush()
        b = Booking(clinic_id=c.id, patient_id=pat.id, practitioner_id=p.id, start=at(22, 9), end=at(22, 9, 30))
        s.add(b)
        s.flush()
        return {"practitioner": p.id, "patient": pat.id, "booking": b.id}


OUT_OF_HOURS = [at(21, h, m) for h, m in [(0, 0), (3, 0), (5, 30), (6, 0), (7, 0), (7, 30), (19, 0), (19, 30), (21, 0), (23, 30)]]
CLOSED_DAYS = [at(25, 10), at(25, 14), at(24, 13), at(24, 15), at(24, 8, 30)]  # Sunday; Saturday afternoon / before 9
OFF_GRID = [at(21, 10, 15), at(21, 11, 45), at(21, 12, 10), datetime(2026, 10, 21, 15, 0, 30)]


@pytest.mark.parametrize("start", OUT_OF_HOURS + CLOSED_DAYS + OFF_GRID)
def test_book_rejects_invalid_times(clinic, start):
    with db.session() as s:
        assert not checker.check(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=start)).allowed


ATTACKS = [
    ("unverified caller", lambda o: (ctx(verified=None), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 11)))),
    ("book for someone else", lambda o: (ctx(), BookProposal(patient_id=DAVID, practitioner_id=TOM, start=at(21, 11)))),
    ("book for a patient id that doesn't exist", lambda o: (ctx(verified=999), BookProposal(patient_id=999, practitioner_id=TOM, start=at(21, 11)))),
    ("book other clinic's patient", lambda o: (ctx(verified=o["patient"]), BookProposal(patient_id=o["patient"], practitioner_id=TOM, start=at(21, 11)))),
    ("book other clinic's practitioner", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=o["practitioner"], start=at(21, 11)))),
    ("book unknown practitioner", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=999, start=at(21, 11)))),
    ("book in the past", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(12, 11)))),
    ("book inside minimum notice", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(19, 10)))),
    ("book too far ahead", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=datetime(2027, 3, 1, 10)))),
    ("double-book a taken slot", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=ELLEN, start=at(21, 14)))),
    ("book over own appointment", lambda o: (ctx(), BookProposal(patient_id=SARAH, practitioner_id=PRIYA, start=at(20, 10)))),
    ("reschedule someone else's booking", lambda o: (ctx(), RescheduleProposal(booking_id=DAVID_NEXT, practitioner_id=TOM, start=at(22, 11)))),
    ("reschedule unknown booking", lambda o: (ctx(), RescheduleProposal(booking_id=999, practitioner_id=TOM, start=at(22, 11)))),
    ("reschedule other clinic's booking", lambda o: (ctx(), RescheduleProposal(booking_id=o["booking"], practitioner_id=TOM, start=at(22, 11)))),
    ("reschedule a past booking", lambda o: (ctx(), RescheduleProposal(booking_id=SARAH_PAST, practitioner_id=TOM, start=at(22, 11)))),
    ("reschedule into a taken slot", lambda o: (ctx(), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=ELLEN, start=at(21, 14)))),
    ("reschedule to the same slot", lambda o: (ctx(), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=TOM, start=at(20, 10)))),
    ("reschedule past closing", lambda o: (ctx(), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=TOM, start=at(22, 18, 45)))),
    ("reschedule to a Sunday", lambda o: (ctx(), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=TOM, start=at(25, 11)))),
    ("reschedule while unverified", lambda o: (ctx(verified=None), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=TOM, start=at(22, 11)))),
    ("reschedule to other clinic's practitioner", lambda o: (ctx(), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=o["practitioner"], start=at(22, 11)))),
    ("cancel someone else's booking", lambda o: (ctx(), CancelProposal(booking_id=DAVID_NEXT))),
    ("cancel unknown booking", lambda o: (ctx(), CancelProposal(booking_id=999))),
    ("cancel other clinic's booking", lambda o: (ctx(), CancelProposal(booking_id=o["booking"]))),
    ("cancel a past booking", lambda o: (ctx(), CancelProposal(booking_id=SARAH_PAST))),
    ("cancel while unverified", lambda o: (ctx(verified=None), CancelProposal(booking_id=SARAH_NEXT))),
    ("erase someone else", lambda o: (ctx(), ForgetProposal(patient_id=DAVID))),
    ("erase other clinic's patient", lambda o: (ctx(verified=o["patient"]), ForgetProposal(patient_id=o["patient"]))),
    ("erase while unverified", lambda o: (ctx(verified=None), ForgetProposal(patient_id=SARAH))),
]


@pytest.mark.parametrize("name,build", ATTACKS, ids=[a[0] for a in ATTACKS])
def test_attacks_are_rejected_and_nothing_changes(other_clinic, name, build):
    caller, proposal = build(other_clinic)
    with db.session() as s:
        before = [(b.id, b.patient_id, b.practitioner_id, b.start, b.status) for b in s.scalars(select(Booking).order_by(Booking.id))]
        patients_before = s.scalar(select(func.count()).select_from(Patient))
        result = propose_and_execute(s, caller, proposal)
        assert not result.ok, name
        after = [(b.id, b.patient_id, b.practitioner_id, b.start, b.status) for b in s.scalars(select(Booking).order_by(Booking.id))]
        assert after == before
        assert s.scalar(select(func.count()).select_from(Patient)) == patients_before
        audit = s.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
        assert audit is not None and audit.allowed is False and audit.executed is False and audit.reasons


def test_clinic_kill_switch_blocks_everything(clinic):
    with db.session() as s:
        s.get(Clinic, clinic).ai_enabled = False
    with db.session() as s:
        d = checker.check(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 11)))
        assert d.reasons == ["clinic_unavailable"]


@pytest.mark.parametrize("flag,proposal", [
    ("allow_reschedule", RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=TOM, start=at(22, 11))),
    ("allow_cancel", CancelProposal(booking_id=SARAH_NEXT)),
])
def test_clinic_policy_flags(clinic, flag, proposal):
    with db.session() as s:
        setattr(s.get(Clinic, clinic), flag, False)
    with db.session() as s:
        assert not checker.check(s, ctx(), proposal).allowed


def test_valid_actions_are_allowed_and_audited(clinic):
    with db.session() as s:
        r = propose_and_execute(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 11)))
        assert r.ok and r.booking_id
        r = propose_and_execute(s, ctx(), RescheduleProposal(booking_id=SARAH_NEXT, practitioner_id=PRIYA, start=at(22, 9, 30)))
        assert r.ok
        moved = s.get(Booking, SARAH_NEXT)
        assert (moved.start, moved.practitioner_id) == (at(22, 9, 30), PRIYA)
        assert s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.executed.is_(True))) == 2


def test_duplicate_and_rate_limit(clinic):
    with db.session() as s:
        p = BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 11))
        assert propose_and_execute(s, ctx(), p).ok
        again = propose_and_execute(s, ctx(), p)
        assert not again.ok and "duplicate_action" in again.decision.reasons
        assert propose_and_execute(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 12))).ok
        assert propose_and_execute(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 13))).ok
        fourth = propose_and_execute(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=at(21, 15)))
        assert not fourth.ok and "too_many_changes_in_one_call" in fourth.decision.reasons


def test_saturday_morning_last_slot_is_valid(clinic):
    with db.session() as s:
        assert checker.check(s, ctx(), BookProposal(patient_id=SARAH, practitioner_id=TOM, start=datetime.combine(date(2026, 10, 24), time(12, 30)))).allowed
