"""Remembering returning callers. Only simple, useful facts: name, usual practitioner, last visit.
Most of it is derived from bookings (so it can never drift from the truth); explicit preferences
live in memory_facts. `forget` lives in tools.booking.forget_patient so erasure has one code path."""

from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.db.models import Booking, MemoryFact, Patient, Practitioner


@dataclass
class CallerMemory:
    patient_id: int
    first_name: str
    usual_practitioner_id: int | None
    usual_practitioner_name: str | None
    last_visit: date | None
    facts: dict[str, str]


def recall(s: Session, clinic_id: int, patient_id: int) -> CallerMemory | None:
    p = s.get(Patient, patient_id)
    if p is None or p.clinic_id != clinic_id:
        return None
    bookings = list(s.scalars(select(Booking).where(Booking.clinic_id == clinic_id, Booking.patient_id == patient_id)))
    past = [b for b in bookings if b.status == "booked" and b.start < clock.now()]
    usual_id = Counter(b.practitioner_id for b in bookings if b.status == "booked").most_common(1)
    usual = s.get(Practitioner, usual_id[0][0]) if usual_id else None
    facts = {f.key: f.value for f in s.scalars(select(MemoryFact).where(
        MemoryFact.clinic_id == clinic_id, MemoryFact.patient_id == patient_id))}
    return CallerMemory(
        patient_id=p.id, first_name=p.first_name,
        usual_practitioner_id=usual.id if usual else None,
        usual_practitioner_name=usual.name if usual else None,
        last_visit=max((b.start.date() for b in past), default=None), facts=facts,
    )


def remember(s: Session, clinic_id: int, patient_id: int, key: str, value: str) -> None:
    fact = s.scalar(select(MemoryFact).where(
        MemoryFact.clinic_id == clinic_id, MemoryFact.patient_id == patient_id, MemoryFact.key == key))
    if fact is None:
        s.add(MemoryFact(clinic_id=clinic_id, patient_id=patient_id, key=key, value=value))
    else:
        fact.value = value
