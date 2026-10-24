"""Caller verification. A phone number alone is never enough to change anything (numbers are
spoofable and shared); we need date of birth plus either the registered phone or the full name."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callbridge.db.models import Patient


def recognise_by_phone(s: Session, clinic_id: int, phone: str | None) -> Patient | None:
    """Unverified recognition, used only to greet and to pre-fill. Ambiguous numbers return None."""
    if not phone:
        return None
    matches = list(s.scalars(select(Patient).where(Patient.clinic_id == clinic_id, Patient.phone == phone)))
    return matches[0] if len(matches) == 1 else None


def verify(s: Session, clinic_id: int, *, dob: date, phone: str | None, first_name: str | None,
           last_name: str | None) -> Patient | None:
    q = select(Patient).where(Patient.clinic_id == clinic_id, Patient.dob == dob)
    candidates = list(s.scalars(q))
    if first_name and last_name:
        named = [p for p in candidates if p.first_name.lower() == first_name.lower() and p.last_name.lower() == last_name.lower()]
        if len(named) == 1:
            return named[0]
    if phone:
        by_phone = [p for p in candidates if p.phone == phone]
        if len(by_phone) == 1 and (not first_name or by_phone[0].first_name.lower() == first_name.lower()):
            return by_phone[0]
    return None


def name_exists(s: Session, clinic_id: int, first_name: str, last_name: str) -> bool:
    return bool(s.scalar(select(func.count()).select_from(Patient).where(
        Patient.clinic_id == clinic_id, func.lower(Patient.first_name) == first_name.lower(),
        func.lower(Patient.last_name) == last_name.lower())))


def register(s: Session, clinic_id: int, *, first_name: str, last_name: str, dob: date, phone: str | None) -> Patient:
    p = Patient(clinic_id=clinic_id, first_name=first_name.title(), last_name=last_name.title(), dob=dob, phone=phone)
    s.add(p)
    s.flush()
    return p
