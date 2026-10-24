"""Row-level security on real Postgres. Runs when CALLBRIDGE_TEST_PG_ADMIN_URL points at a scratch
database (CI starts one; locally: docker run -e POSTGRES_PASSWORD=pw -p 55432:5432 pgvector/pgvector:pg17).
The app connects as callbridge_app (no BYPASSRLS), exactly like production."""

import os
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, func, select, text

from callbridge import clock, db
from callbridge.db.models import Booking, Clinic, Message, Patient, Practitioner
from callbridge.db.seed import seed_demo

ADMIN = os.environ.get("CALLBRIDGE_TEST_PG_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN, reason="set CALLBRIDGE_TEST_PG_ADMIN_URL to run Postgres RLS tests")
NOW = datetime(2026, 10, 19, 9, 0)


@pytest.fixture
def pg():
    admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        c.execute(text(open("infra/sql/000_roles.sql").read()))
        c.execute(text("ALTER ROLE callbridge_app PASSWORD 'app-test-pw'"))
    app_url = ADMIN.split("://")[0] + "://callbridge_app:app-test-pw@" + ADMIN.split("@", 1)[1]
    with clock.frozen_at(NOW):
        with db.isolated(ADMIN):  # migrations run as the owner
            db.init_db()
        with db.isolated(ADMIN):
            with db.session() as s:
                a = seed_demo(s, NOW).id
            with db.session() as s:
                b = Clinic(name="Other Clinic", slug="other", phone_number="+441170000009")
                s.add(b)
                s.flush()
                from callbridge.db.session import scope

                scope(s, b.id)
                s.add(Patient(clinic_id=b.id, first_name="Bea", last_name="Other", dob=date(1990, 1, 1), phone="+447700900888"))
                s.add(Message(clinic_id=b.id, body="other clinic's private message"))
                b = b.id
        with db.isolated(app_url):
            yield a, b


def test_each_clinic_sees_only_its_own_rows(pg):
    a, b = pg
    with db.session(a) as s:
        assert {p.first_name for p in s.scalars(select(Patient))} == {"Sarah", "David"}
        assert all(m.clinic_id == a for m in s.scalars(select(Message)))
    with db.session(b) as s:
        assert {p.first_name for p in s.scalars(select(Patient))} == {"Bea"}
        assert s.scalar(select(func.count()).select_from(Booking)) == 0


def test_no_clinic_set_means_no_rows(pg):
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Patient)) == 0


def test_cannot_write_into_another_clinic(pg):
    a, b = pg
    with pytest.raises(Exception, match="row-level security"):
        with db.session(a) as s:
            s.add(Message(clinic_id=b, body="smuggled"))


def test_full_call_works_under_rls(pg):
    from callbridge.agent.session import CallSession

    a, _ = pg
    with clock.frozen_at(NOW):
        call = CallSession("pg-call-1", a, "+447700900123")
        call.start()
        for line in ["I need to cancel my appointment", "12th March 1985", "yes"]:
            reply = call.turn(line).reply
        assert reply.startswith("Done")
        call.end("completed")
    with db.session(a) as s:
        assert s.scalar(select(Booking).where(Booking.status == "cancelled")) is not None
        assert s.scalar(select(func.count()).select_from(Practitioner)) == 4
