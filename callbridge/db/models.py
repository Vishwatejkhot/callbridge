"""Database schema. Every clinic-owned table carries clinic_id and is protected by Postgres RLS
(see infra/sql/001_rls.sql). Datetimes are naive clinic-local time (all clinics are in the UK)."""

from datetime import date, datetime, time

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from callbridge import clock


class Base(DeclarativeBase):
    pass


class Clinic(Base):
    __tablename__ = "clinics"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True)  # Twilio number calls are forwarded to
    transfer_number: Mapped[str | None] = mapped_column(String(32))  # where human handoffs are dialled
    appointment_minutes: Mapped[int] = mapped_column(Integer, default=45)
    allow_reschedule: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_cancel: Mapped[bool] = mapped_column(Boolean, default=True)
    min_notice_hours: Mapped[int] = mapped_column(Integer, default=2)
    max_days_ahead: Mapped[int] = mapped_column(Integer, default=60)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # clinic kill switch

    practitioners: Mapped[list["Practitioner"]] = relationship(back_populates="clinic")
    hours: Mapped[list["OpeningHours"]] = relationship(back_populates="clinic")


class Practitioner(Base):
    __tablename__ = "practitioners"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    service: Mapped[str] = mapped_column(String(80))  # physio, osteopathy, sports massage...
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    clinic: Mapped[Clinic] = relationship(back_populates="practitioners")


class OpeningHours(Base):
    __tablename__ = "opening_hours"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    clinic: Mapped[Clinic] = relationship(back_populates="hours")


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    dob: Mapped[date] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    practitioner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    end: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="booked")  # booked | cancelled
    created_by: Mapped[str] = mapped_column(String(16), default="ai")  # ai | staff
    call_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, onupdate=clock.now)

    patient: Mapped[Patient] = relationship()
    practitioner: Mapped[Practitioner] = relationship()


class MemoryFact(Base):
    """Small, explicit facts remembered about a caller (preferences), beyond what bookings imply."""

    __tablename__ = "memory_facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, onupdate=clock.now)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    topic: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Twilio CallSid or sim id
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    caller_phone: Mapped[str | None] = mapped_column(String(32))
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | ended | taken_over
    outcome: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    rollout_arm: Mapped[str | None] = mapped_column(String(16))  # live | canary
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # PII-redacted
    events: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[list] = mapped_column(JSON, default=list)
    cost_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    failure_reasons: Mapped[list] = mapped_column(JSON, default=list)
    staff_correction: Mapped[str | None] = mapped_column(Text)
    recording_url: Mapped[str | None] = mapped_column(String(500))


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(64))
    caller_name: Mapped[str | None] = mapped_column(String(160))
    caller_phone: Mapped[str | None] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)


class SmsLog(Base):
    __tablename__ = "sms_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    to: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    provider_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)


class AuditLog(Base):
    """Every proposal the AI makes and the checker's verdict. Append-only."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))
    proposal: Mapped[dict] = mapped_column(JSON)
    allowed: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)


class StaffUser(Base):
    __tablename__ = "staff_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="staff")  # staff | owner


# ---- Improvement loop (not clinic-scoped: contains only redacted, synthetic-ised data) ----


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False  # not a pytest class
    id: Mapped[int] = mapped_column(primary_key=True)
    source_call_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(64))
    scenario: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | in_suite | fixed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    parent: Mapped[str | None] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str | None] = mapped_column(Text)
    # draft -> gated -> shadow -> approved -> canary -> live | rejected | rolled_back | retired
    status: Mapped[str] = mapped_column(String(16), default="draft")
    eval_report: Mapped[dict | None] = mapped_column(JSON)
    shadow_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, onupdate=clock.now)


CLINIC_SCOPED_TABLES = [
    "practitioners", "opening_hours", "patients", "bookings", "memory_facts", "knowledge_chunks",
    "calls", "messages", "sms_log", "audit_log",
]
