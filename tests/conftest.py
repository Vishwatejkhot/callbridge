import os
from datetime import datetime

import pytest

os.environ.setdefault("CALLBRIDGE_LLM_PROVIDER", "rules")
os.environ.setdefault("CALLBRIDGE_DATABASE_URL", "sqlite://")

from callbridge import clock, db  # noqa: E402
from callbridge.db.seed import seed_demo  # noqa: E402

# Monday 19 Oct 2026, 09:00. Sarah Jones (12/03/1985, +447700900123) has Tom on Tue 20th 10:00.
NOW = datetime(2026, 10, 19, 9, 0)
SARAH_PHONE = "+447700900123"


@pytest.fixture
def clinic():
    """A fresh in-memory database with the fictional demo clinic, at a pinned time."""
    with clock.frozen_at(NOW), db.isolated("sqlite://"):
        db.init_db()
        with db.session() as s:
            c = seed_demo(s, NOW)
            clinic_id = c.id
        yield clinic_id


@pytest.fixture
def call(clinic):
    from callbridge.agent.session import CallSession

    def _make(phone: str | None = SARAH_PHONE, **kw):
        session = CallSession(f"test-{os.urandom(4).hex()}", clinic, phone, **kw)
        session.start()
        return session

    return _make
