"""The only things the AI is allowed to ask for. The LLM never touches the database: it produces
one of these, the checker validates it against the verified caller, then tools execute it."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class BookProposal(BaseModel):
    action: Literal["book"] = "book"
    patient_id: int
    practitioner_id: int
    start: datetime


class RescheduleProposal(BaseModel):
    action: Literal["reschedule"] = "reschedule"
    booking_id: int
    practitioner_id: int
    start: datetime


class CancelProposal(BaseModel):
    action: Literal["cancel"] = "cancel"
    booking_id: int


class ForgetProposal(BaseModel):
    """Caller asked us to delete their data (UK GDPR right to erasure)."""

    action: Literal["forget"] = "forget"
    patient_id: int


Proposal = Annotated[BookProposal | RescheduleProposal | CancelProposal | ForgetProposal, Field(discriminator="action")]


class CallerContext(BaseModel):
    """What the system (not the LLM) knows about who is on the line."""

    call_id: str
    clinic_id: int
    caller_phone: str | None = None
    verified_patient_id: int | None = None
    patient_created_this_call: bool = False


class Decision(BaseModel):
    allowed: bool
    reasons: list[str] = []
