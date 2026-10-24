"""The booking tools as an MCP server, so any MCP-capable agent (or the staff's own assistant) can use
them. Same rule as everywhere else: read tools are open, write tools go through the safety checker,
and the *caller identity* comes from the call record on our side, never from the tool arguments."""

from datetime import date, datetime

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from callbridge import db
from callbridge.agent.session import CallSession
from callbridge.calls import registry
from callbridge.db.models import Call
from callbridge.safety.proposals import BookProposal, CallerContext, CancelProposal, RescheduleProposal
from callbridge.tools import booking

mcp = MCPServer(name="callbridge-booking", instructions=(
    "Booking tools for one clinic. Use check_slots to find availability. book/reschedule/cancel only work for "
    "a caller the phone system has already verified on the given call_id; every change is re-checked by code."))


def _ctx(call_id: str) -> CallerContext:
    """Identity comes from the live call (verified by our own flow), never from the model."""
    live: CallSession | None = registry.get(call_id)
    if live is not None and live.state is not None:
        st = live.state
        return CallerContext(call_id=call_id, clinic_id=live.clinic_id, caller_phone=live.caller_phone,
                             verified_patient_id=st.get("verified_patient_id"))
    with db.session() as s:
        call = s.get(Call, call_id)
        if call is None:
            raise ValueError("unknown call")
        return CallerContext(call_id=call_id, clinic_id=call.clinic_id, caller_phone=call.caller_phone, verified_patient_id=None)


@mcp.tool()
def check_slots(clinic_id: int, day: str | None = None, practitioner_id: int | None = None, service: str | None = None,
                daypart: str | None = None, limit: int = 3) -> list[dict]:
    """Free appointment slots. day is YYYY-MM-DD (omit for the next available day)."""
    with db.session(clinic_id) as s:
        slots = booking.check_slots(s, clinic_id, day=date.fromisoformat(day) if day else None,
                                    practitioner_id=practitioner_id, service=service, daypart=daypart, limit=min(limit, 10))
        return [sl.to_dict() for sl in slots]


@mcp.tool()
def list_practitioners(clinic_id: int) -> list[dict]:
    """Practitioners at the clinic and their service."""
    with db.session(clinic_id) as s:
        return [{"id": p.id, "name": p.name, "service": p.service} for p in booking.practitioners(s, clinic_id)]


def _execute(call_id: str, proposal) -> dict:
    ctx = _ctx(call_id)
    with db.session(ctx.clinic_id) as s:
        result = booking.propose_and_execute(s, ctx, proposal)
        return {"ok": result.ok, "reasons": result.decision.reasons, "booking_id": result.booking_id, "sms_sent": result.sms_sent}


@mcp.tool()
def book(call_id: str, practitioner_id: int, start: str) -> dict:
    """Book the verified caller on call_id into a slot. start is ISO datetime, e.g. 2026-10-29T15:00."""
    ctx = _ctx(call_id)
    return _execute(call_id, BookProposal(patient_id=ctx.verified_patient_id or -1, practitioner_id=practitioner_id,
                                          start=datetime.fromisoformat(start)))


@mcp.tool()
def reschedule(call_id: str, booking_id: int, practitioner_id: int, start: str) -> dict:
    """Move one of the verified caller's bookings."""
    return _execute(call_id, RescheduleProposal(booking_id=booking_id, practitioner_id=practitioner_id,
                                                start=datetime.fromisoformat(start)))


@mcp.tool()
def cancel(call_id: str, booking_id: int) -> dict:
    """Cancel one of the verified caller's bookings."""
    return _execute(call_id, CancelProposal(booking_id=booking_id))


@mcp.tool()
def send_sms_confirmation(call_id: str, booking_id: int) -> dict:
    """Re-send the confirmation text for one of the verified caller's bookings."""
    ctx = _ctx(call_id)
    with db.session(ctx.clinic_id) as s:
        b = s.scalar(select(booking.Booking).where(booking.Booking.id == booking_id, booking.Booking.clinic_id == ctx.clinic_id))
        if b is None or ctx.verified_patient_id is None or b.patient_id != ctx.verified_patient_id or not ctx.caller_phone:
            return {"ok": False, "reasons": ["not_allowed"]}
        sent = booking.sms.send(s, ctx.clinic_id, ctx.caller_phone,
                                f"Reminder: your appointment is {booking.speak_on(b.start)}.")
        return {"ok": sent}


def main() -> None:
    db.init_db()
    mcp.run()
