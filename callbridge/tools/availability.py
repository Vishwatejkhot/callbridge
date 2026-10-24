"""Pure availability logic shared by the slot finder (what to offer) and the safety checker
(is this really free?). Keeping one implementation means they can never disagree."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.db.models import Booking, Clinic, OpeningHours, Practitioner


def opening_window(s: Session, clinic: Clinic, day: date) -> tuple[datetime, datetime] | None:
    oh = s.scalar(select(OpeningHours).where(OpeningHours.clinic_id == clinic.id, OpeningHours.weekday == day.weekday()))
    if oh is None:
        return None
    return datetime.combine(day, oh.opens), datetime.combine(day, oh.closes)


def within_opening_hours(s: Session, clinic: Clinic, start: datetime) -> bool:
    window = opening_window(s, clinic, start.date())
    if window is None:
        return False
    end = start + timedelta(minutes=clinic.appointment_minutes)
    return window[0] <= start and end <= window[1]


def on_grid(clinic: Clinic, start: datetime) -> bool:
    minutes = start.hour * 60 + start.minute
    return start.second == 0 and minutes % clinic.appointment_minutes == 0


def overlapping(s: Session, clinic_id: int, practitioner_id: int, start: datetime, end: datetime,
                ignore_booking_id: int | None = None) -> list[Booking]:
    q = select(Booking).where(
        Booking.clinic_id == clinic_id, Booking.practitioner_id == practitioner_id,
        Booking.status == "booked", Booking.start < end, Booking.end > start,
    )
    if ignore_booking_id is not None:
        q = q.where(Booking.id != ignore_booking_id)
    return list(s.scalars(q))


def earliest_allowed(clinic: Clinic) -> datetime:
    return clock.now() + timedelta(hours=clinic.min_notice_hours)


def free_slots(s: Session, clinic: Clinic, practitioners: list[Practitioner], day: date,
               ignore_booking_id: int | None = None) -> list[tuple[datetime, Practitioner]]:
    window = opening_window(s, clinic, day)
    if window is None or day > (clock.now() + timedelta(days=clinic.max_days_ahead)).date():
        return []
    step = timedelta(minutes=clinic.appointment_minutes)
    earliest = earliest_allowed(clinic)
    out: list[tuple[datetime, Practitioner]] = []
    t = window[0]
    while t + step <= window[1]:
        if t >= earliest:
            for p in practitioners:
                if p.active and not overlapping(s, clinic.id, p.id, t, t + step, ignore_booking_id):
                    out.append((t, p))
        t += step
    return out
