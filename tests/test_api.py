from datetime import date

import pytest
from fastapi.testclient import TestClient

from callbridge import db
from callbridge.api.auth import hash_password
from callbridge.db.models import Clinic, Message, StaffUser
from callbridge.db.seed import DEMO_CLINIC_NUMBER


@pytest.fixture
def client(clinic):
    from callbridge.api.app import app

    with TestClient(app) as c:
        yield c


def login(client, email="demo@harbourside.example", password="demo-password"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_rejects_bad_password(client):
    assert client.post("/api/auth/login", json={"email": "demo@harbourside.example", "password": "nope"}).status_code == 401


def test_endpoints_require_login(client):
    for path in ["/api/calls/live", "/api/messages", "/api/bookings", "/api/calls"]:
        assert client.get(path).status_code == 401


def test_simulated_call_shows_live_then_in_history(client):
    h = login(client)
    start = client.post("/api/sim/calls", json={"caller_phone": "+447700900123"}, headers=h).json()
    assert "AI assistant" in start["reply"]
    live = client.get("/api/calls/live", headers=h).json()
    assert [c["call_id"] for c in live] == [start["call_id"]]
    turn = client.post(f"/api/sim/calls/{start['call_id']}/turn", json={"text": "I need to cancel my appointment"}, headers=h).json()
    assert "date of birth" in turn["reply"]
    client.post(f"/api/sim/calls/{start['call_id']}/end", headers=h)
    assert client.get("/api/calls/live", headers=h).json() == []
    history = client.get("/api/calls", headers=h).json()
    assert history[0]["id"] == start["call_id"] and "hangup_mid_task" in history[0]["failure_reasons"]


def test_takeover_returns_summary(client):
    h = login(client)
    cid = client.post("/api/sim/calls", json={"caller_phone": "+447700900123"}, headers=h).json()["call_id"]
    client.post(f"/api/sim/calls/{cid}/turn", json={"text": "I want to move my appointment"}, headers=h)
    client.post(f"/api/sim/calls/{cid}/turn", json={"text": "12th March 1985"}, headers=h)
    r = client.post(f"/api/calls/{cid}/takeover", headers=h).json()
    assert "Sarah Jones (verified)" in r["summary"] and "reschedule" in r["summary"]


def test_staff_only_see_their_own_clinic(client):
    with db.session() as s:
        other = Clinic(name="Other Physio", slug="other", phone_number="+441170000009")
        s.add(other)
        s.flush()
        s.add(StaffUser(clinic_id=other.id, email="staff@other.example", password_hash=hash_password("pw-other-123"), role="owner"))
        s.add(Message(clinic_id=1, body="private to Harbourside", caller_phone="+447700900321"))
    demo, intruder = login(client), login(client, "staff@other.example", "pw-other-123")
    cid = client.post("/api/sim/calls", json={}, headers=demo).json()["call_id"]
    assert client.get("/api/calls/live", headers=intruder).json() == []
    assert client.post(f"/api/calls/{cid}/takeover", headers=intruder).status_code == 404
    assert client.get(f"/api/calls/{cid}", headers=intruder).status_code == 404
    assert all("Harbourside" not in m["body"] for m in client.get("/api/messages", headers=intruder).json())


def test_twilio_voice_webhook_streams_to_websocket(client):
    r = client.post("/twilio/voice", data={"To": DEMO_CLINIC_NUMBER, "From": "+447700900123", "CallSid": "CA1"})
    assert r.status_code == 200 and "<Connect" in r.text and "/ws/twilio" in r.text and 'name="clinic_id" value="1"' in r.text


def test_kill_switch_sends_calls_to_voicemail(client):
    h = login(client)
    assert client.post("/api/clinic/ai", json={"enabled": False}, headers=h).json() == {"ai_enabled": False}
    r = client.post("/twilio/voice", data={"To": DEMO_CLINIC_NUMBER, "From": "+447700900123"})
    assert "<Record" in r.text and "<Connect" not in r.text


def test_after_stream_falls_back_to_voicemail_unless_finished(client):
    from callbridge.voice import telephony

    assert "<Record" in client.post("/twilio/after-stream", data={"CallSid": "CAx"}).text
    telephony.mark_finished("CAy", normally=True)
    assert "<Hangup/>" in client.post("/twilio/after-stream", data={"CallSid": "CAy"}).text


def test_metrics_exposed(client):
    assert "callbridge_calls_total" in client.get("/metrics").text


def test_reports_owner_only(client):
    with db.session() as s:
        s.add(StaffUser(clinic_id=1, email="frontdesk@harbourside.example", password_hash=hash_password("pw-staff-123"), role="staff"))
    assert client.get("/api/reports", headers=login(client, "frontdesk@harbourside.example", "pw-staff-123")).status_code == 403
    assert client.get("/api/reports", headers=login(client)).status_code == 200


_ = date
