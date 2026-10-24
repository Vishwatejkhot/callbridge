"""Twilio call control: TwiML, transfers to staff, recording, the never-silent fallback."""

import logging
import threading
from xml.sax.saxutils import escape, quoteattr

from callbridge import db
from callbridge.calls import registry
from callbridge.config import get_settings
from callbridge.db.models import Clinic

log = logging.getLogger(__name__)
_finished_normally: dict[str, bool] = {}
_lock = threading.Lock()

FALLBACK_SAY = ("Sorry, we're having a technical problem. Please leave a short message with your name and number "
                "after the tone, and the clinic will call you back.")


def _client():
    st = get_settings()
    if not (st.twilio_account_sid and st.twilio_auth_token):
        return None
    from twilio.rest import Client

    return Client(st.twilio_account_sid, st.twilio_auth_token)


def stream_twiml(ws_url: str, clinic_id: int, caller: str | None, fallback_url: str) -> str:
    """Connect the call audio to our websocket. When the stream ends, Twilio requests `fallback_url`."""
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>"
        f"<Connect action={quoteattr(fallback_url)}><Stream url={quoteattr(ws_url)}>"
        f"<Parameter name=\"clinic_id\" value=\"{clinic_id}\"/>"
        f"<Parameter name=\"from\" value={quoteattr(caller or '')}/>"
        "</Stream></Connect></Response>"
    )


def voicemail_twiml(text: str = FALLBACK_SAY) -> str:
    return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say voice=\"Polly.Amy\" language=\"en-GB\">{escape(text)}</Say>"
            "<Record maxLength=\"120\" playBeep=\"true\"/><Hangup/></Response>")


def dial_twiml(number: str, intro: str = "Connecting you to a member of the team.") -> str:
    return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say voice=\"Polly.Amy\" language=\"en-GB\">{escape(intro)}</Say>"
            f"<Dial>{escape(number)}</Dial></Response>")


def mark_finished(call_sid: str, normally: bool) -> None:
    with _lock:
        _finished_normally[call_sid] = normally


def after_stream_twiml(call_sid: str) -> str:
    """Called by Twilio when the media stream ends. Normal end -> hang up; anything else -> voicemail."""
    with _lock:
        normally = _finished_normally.pop(call_sid, False)
    if normally:
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Hangup/></Response>"
    log.warning("stream ended unexpectedly for a call; falling back to voicemail")
    return voicemail_twiml()


def transfer_to_staff(call_sid: str, clinic_id: int) -> bool:
    """Redirect the live call to the clinic's staff number (one-click takeover or caller request)."""
    with db.session(clinic_id) as s:
        clinic = s.get(Clinic, clinic_id)
        number = clinic.transfer_number if clinic else None
    session = registry.get(call_sid)
    if session is not None:
        session.mark_taken_over()
    mark_finished(call_sid, normally=True)
    client = _client()
    if client is None or not number:
        return False
    try:
        client.calls(call_sid).update(twiml=dial_twiml(number))
        return True
    except Exception:
        log.exception("transfer failed")
        return False


def start_recording(call_sid: str, clinic_id: int) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.calls(call_sid).recordings.create(
            recording_status_callback=f"{get_settings().public_base_url}/twilio/recording?clinic_id={clinic_id}",
            recording_channels="dual")
    except Exception:
        log.exception("could not start recording")
