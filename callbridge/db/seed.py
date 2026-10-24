"""Fictional demo clinic. Never put real patient data here: names are invented and phone numbers
are from Ofcom's 07700 900xxx range, which is reserved for drama and never assigned."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from callbridge import clock
from callbridge.db.models import (
    Booking, Clinic, KnowledgeChunk, OpeningHours, Patient, Practitioner, StaffUser,
)

DEMO_CLINIC_NUMBER = "+441170000000"

KNOWLEDGE = {
    "opening hours": "We're open Monday to Friday 8am to 7pm, and Saturday 9am to 1pm. We're closed on Sundays and bank holidays.",
    "address location": "We're at 12 Harbourside Walk, Bristol, BS1 5XX, opposite the ferry landing.",
    "parking": "There's no parking at the clinic itself, but the Harbourside multi-storey car park is a two minute walk away. There are two disabled bays right outside the entrance.",
    "prices cost fees price": "A first physio assessment is 65 pounds and lasts 45 minutes. Follow-up physio sessions are 50 pounds. Osteopathy is 60 pounds, and a sports massage is 55 pounds.",
    "insurance bupa axa vitality aviva": "We're recognised by Bupa, AXA Health, Vitality and Aviva. Please bring your authorisation number to your appointment.",
    "cancellation policy late cancel fee": "We ask for 24 hours notice to cancel or move an appointment. Late cancellations may be charged in full.",
    "payment pay card cash": "You can pay by card or contactless at the end of your session. We don't take cash.",
    "what to bring wear clothes": "Please wear loose, comfortable clothing and bring any scans or letters about your injury.",
    "accessibility wheelchair step-free lift": "The clinic is step-free with a lift to all treatment rooms and an accessible toilet.",
    "services treatments offer": "We offer physiotherapy, osteopathy and sports massage.",
}


def seed_demo(s: Session, now: datetime | None = None) -> Clinic:
    """Create the demo clinic if missing. Idempotent."""
    existing = s.scalar(select(Clinic).where(Clinic.slug == "harbourside-physio"))
    if existing:
        return existing
    now = now or clock.now()

    clinic = Clinic(
        name="Harbourside Physio", slug="harbourside-physio", phone_number=DEMO_CLINIC_NUMBER,
        transfer_number=None, appointment_minutes=30, min_notice_hours=2, max_days_ahead=60,
    )
    s.add(clinic)
    s.flush()
    from callbridge.db.session import scope

    scope(s, clinic.id)  # the rest of the rows are clinic-owned (RLS on Postgres)
    for wd in range(5):
        s.add(OpeningHours(clinic_id=clinic.id, weekday=wd, opens=time(8), closes=time(19)))
    s.add(OpeningHours(clinic_id=clinic.id, weekday=5, opens=time(9), closes=time(13)))

    tom = Practitioner(clinic_id=clinic.id, name="Tom Reed", service="physio")
    priya = Practitioner(clinic_id=clinic.id, name="Priya Shah", service="physio")
    ellen = Practitioner(clinic_id=clinic.id, name="Ellen Marsh", service="osteopathy")
    jake = Practitioner(clinic_id=clinic.id, name="Jake Lowe", service="sports massage")
    s.add_all([tom, priya, ellen, jake])

    for topic, text in KNOWLEDGE.items():
        s.add(KnowledgeChunk(clinic_id=clinic.id, topic=topic, text=text))

    sarah = Patient(clinic_id=clinic.id, first_name="Sarah", last_name="Jones", dob=date(1985, 3, 12), phone="+447700900123")
    david = Patient(clinic_id=clinic.id, first_name="David", last_name="Okafor", dob=date(1972, 11, 2), phone="+447700900456")
    s.add_all([sarah, david])
    s.flush()

    def at(days: int, hour: int, minute: int = 0) -> datetime:
        d = (now + timedelta(days=days)).date()
        while d.weekday() >= 5:  # keep demo bookings on weekdays
            d += timedelta(days=1)
        return datetime.combine(d, time(hour, minute))

    mins = timedelta(minutes=clinic.appointment_minutes)
    for b in [
        Booking(clinic_id=clinic.id, patient_id=sarah.id, practitioner_id=tom.id, start=at(-14, 10), created_by="staff"),
        Booking(clinic_id=clinic.id, patient_id=sarah.id, practitioner_id=tom.id, start=at(1, 10), created_by="staff"),
        Booking(clinic_id=clinic.id, patient_id=david.id, practitioner_id=ellen.id, start=at(2, 14), created_by="staff"),
        Booking(clinic_id=clinic.id, patient_id=david.id, practitioner_id=priya.id, start=at(-30, 9), created_by="staff"),
    ]:
        b.end = b.start + mins
        s.add(b)

    from callbridge.api.auth import hash_password

    s.add(StaffUser(clinic_id=clinic.id, email="demo@harbourside.example", password_hash=hash_password("demo-password"), role="owner"))
    s.flush()
    return clinic
