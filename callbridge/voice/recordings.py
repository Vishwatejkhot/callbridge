"""Call recordings live in Twilio (UK region). We store only the URL; deletion goes to Twilio."""

import logging
import re

from callbridge.config import get_settings

log = logging.getLogger(__name__)


def delete_recording(url: str) -> bool:
    st = get_settings()
    m = re.search(r"/Recordings/(RE[0-9a-f]{32})", url)
    if not (m and st.twilio_account_sid and st.twilio_auth_token):
        return False
    try:
        from twilio.rest import Client

        return bool(Client(st.twilio_account_sid, st.twilio_auth_token).recordings(m[1]).delete())
    except Exception:
        log.exception("recording delete failed")
        return False
