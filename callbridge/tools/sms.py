"""SMS confirmations via Twilio. Every SMS is logged first, so a Twilio outage never loses one:
failed rows stay 'failed' and are visible to staff."""

import logging

from sqlalchemy.orm import Session

from callbridge.config import get_settings
from callbridge.db.models import SmsLog

log = logging.getLogger(__name__)


def send(s: Session, clinic_id: int, to: str, body: str) -> bool:
    row = SmsLog(clinic_id=clinic_id, to=to, body=body)
    s.add(row)
    s.flush()
    st = get_settings()
    if not (st.twilio_account_sid and st.twilio_auth_token and st.twilio_sms_from):
        row.status = "simulated"  # dev / evals: recorded but not sent
        return True
    try:
        from twilio.rest import Client

        msg = Client(st.twilio_account_sid, st.twilio_auth_token).messages.create(to=to, from_=st.twilio_sms_from, body=body)
        row.provider_id, row.status = msg.sid, "sent"
        return True
    except Exception:  # never let an SMS failure break the call
        log.exception("sms send failed")
        row.status = "failed"
        return False
