"""Scheduled jobs (EventBridge -> ECS task in production, see infra/terraform/jobs.tf)."""

from datetime import timedelta

from sqlalchemy import select

from callbridge import clock, db
from callbridge.config import get_settings
from callbridge.db.models import Call
from callbridge.voice.recordings import delete_recording

TRANSCRIPT_RETENTION_DAYS = 90


def purge_expired() -> dict:
    """Retention policy (docs/compliance.md): recordings 30 days, transcripts 90 days."""
    st = get_settings()
    now = clock.now()
    recordings = transcripts = 0
    with db.session() as s:  # runs as callbridge_ops (BYPASSRLS): spans all clinics
        for c in s.scalars(select(Call).where(Call.recording_url.is_not(None),
                                              Call.started_at < now - timedelta(days=st.recording_retention_days))):
            delete_recording(c.recording_url)
            c.recording_url = None
            recordings += 1
        for c in s.scalars(select(Call).where(Call.started_at < now - timedelta(days=TRANSCRIPT_RETENTION_DAYS))):
            if c.transcript:
                c.transcript, transcripts = [], transcripts + 1
    return {"recordings_deleted": recordings, "transcripts_cleared": transcripts}
