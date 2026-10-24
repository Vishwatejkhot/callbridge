"""FastAPI app: Twilio webhooks + media websocket, staff dashboard API, text simulator, metrics."""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sqlalchemy import func, select, text

from callbridge import clock, db
from callbridge.agent.session import CallSession
from callbridge.api.auth import Staff, check_password, current_staff, decode, issue_token, owner_only
from callbridge.calls import registry
from callbridge.config import get_settings
from callbridge.db.models import Booking, Call, Clinic, Message, Patient, Practitioner, StaffUser
from callbridge.improver import rollout
from callbridge.observability import setup
from callbridge.tools.booking import forget_patient
from callbridge.voice import telephony

log = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.env != "dev" and settings.jwt_secret.startswith("dev-only"):
        raise RuntimeError("CALLBRIDGE_JWT_SECRET must be set outside dev")
    setup()
    db.init_db()
    yield


app = FastAPI(title="Callbridge", version="0.1.0", lifespan=lifespan,
              docs_url=None if settings.env == "production" else "/docs")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"] if settings.env == "dev" else [],
                   allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------ middleware

_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limit_and_headers(request: Request, call_next):
    if not request.url.path.startswith(("/twilio", "/healthz", "/metrics")):
        key = request.client.host if request.client else "?"
        now, window = time.monotonic(), _hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= settings.rate_limit_per_minute:
            return PlainTextResponse("rate limited", status_code=429)
        window.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------ health + metrics


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    with db.session() as s:
        s.execute(text("SELECT 1"))
    return {"ok": True, "live_calls": sum(len(registry.live(c)) for c in _clinic_ids())}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _clinic_ids() -> list[int]:
    with db.session() as s:
        return list(s.scalars(select(Clinic.id)))


# ------------------------------------------------------------ Twilio


async def _validated_form(request: Request) -> dict:
    form = dict(await request.form())
    if settings.twilio_auth_token:
        from twilio.request_validator import RequestValidator

        url = f"{settings.public_base_url}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
        signature = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(settings.twilio_auth_token).validate(url, form, signature):
            raise HTTPException(403, "bad signature")
    elif settings.env == "production":
        raise HTTPException(500, "twilio auth token not configured")
    return form


@app.post("/twilio/voice")
async def twilio_voice(request: Request):
    form = await _validated_form(request)
    with db.session() as s:
        clinic = s.scalar(select(Clinic).where(Clinic.phone_number == form.get("To")))
        if clinic is None:
            return Response(telephony.voicemail_twiml("Sorry, this number isn't set up yet."), media_type="application/xml")
        if not clinic.ai_enabled:  # clinic switched the AI off: straight to staff, or voicemail
            twiml = telephony.dial_twiml(clinic.transfer_number) if clinic.transfer_number else telephony.voicemail_twiml(
                f"Thanks for calling {clinic.name}. Please leave a message and we'll call you back.")
            return Response(twiml, media_type="application/xml")
        clinic_id = clinic.id
    ws_url = settings.public_base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws/twilio"
    twiml = telephony.stream_twiml(ws_url, clinic_id, form.get("From"), f"{settings.public_base_url}/twilio/after-stream")
    return Response(twiml, media_type="application/xml")


@app.post("/twilio/after-stream")
async def twilio_after_stream(request: Request):
    form = await _validated_form(request)
    return Response(telephony.after_stream_twiml(form.get("CallSid", "")), media_type="application/xml")


@app.post("/twilio/recording")
async def twilio_recording(request: Request, clinic_id: int):
    form = await _validated_form(request)  # signature covers the query string, so clinic_id can't be forged
    with db.session(clinic_id) as s:
        call = s.get(Call, form.get("CallSid", ""))
        if call is not None and form.get("RecordingUrl"):
            call.recording_url = form["RecordingUrl"]
    return {"ok": True}


@app.websocket("/ws/twilio")
async def twilio_media(websocket: WebSocket):
    await websocket.accept()
    from callbridge.voice.pipeline import run_twilio_call

    await run_twilio_call(websocket)


# ------------------------------------------------------------ staff auth


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn):
    with db.session() as s:
        user = s.scalar(select(StaffUser).where(StaffUser.email == body.email.lower().strip()))
        if user is None or not check_password(body.password, user.password_hash):
            time.sleep(0.3)  # blunt brute force
            raise HTTPException(401, "wrong email or password")
        clinic = s.get(Clinic, user.clinic_id)
        return {"token": issue_token(user.id, user.clinic_id, user.email, user.role),
                "clinic": {"id": clinic.id, "name": clinic.name, "ai_enabled": clinic.ai_enabled}, "role": user.role}


# ------------------------------------------------------------ live calls


@app.get("/api/calls/live")
def live_calls(staff: Staff = Depends(current_staff)):
    return [c.snapshot() for c in registry.live(staff.clinic_id)]


@app.get("/api/events")
async def events(token: str):
    """Server-sent events for the dashboard (EventSource can't send headers, so the token is a query param)."""
    staff = decode(token)
    q = registry.subscribe()

    async def stream():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if ev.get("clinic_id") in (None, staff.clinic_id):
                    yield f"data: {json.dumps(ev)}\n\n"
        finally:
            registry.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/calls/{call_id}/takeover")
def takeover(call_id: str, staff: Staff = Depends(current_staff)):
    session = registry.get(call_id)
    if session is None or session.clinic_id != staff.clinic_id:
        raise HTTPException(404, "call not live")
    snapshot = session.snapshot()
    handler = registry.takeover_handler(call_id)
    transferred = bool(handler and handler())
    if handler is None:
        session.mark_taken_over()
    log.info("call taken over by staff")
    return {"transferred": transferred, "summary": snapshot["summary"], "transcript": snapshot["transcript"]}


@app.get("/api/calls")
def call_history(limit: int = 50, staff: Staff = Depends(current_staff)):
    with db.session(staff.clinic_id) as s:
        rows = s.scalars(select(Call).where(Call.clinic_id == staff.clinic_id).order_by(Call.started_at.desc()).limit(min(limit, 200)))
        return [{"id": c.id, "started_at": c.started_at, "ended_at": c.ended_at, "caller_phone": c.caller_phone,
                 "outcome": c.outcome, "summary": c.summary, "confidence": c.confidence,
                 "failure_reasons": c.failure_reasons, "prompt_version": c.prompt_version} for c in rows]


@app.get("/api/calls/{call_id}")
def call_detail(call_id: str, staff: Staff = Depends(current_staff)):
    live = registry.get(call_id)
    if live is not None and live.clinic_id == staff.clinic_id:
        return {"live": True, **live.snapshot()}
    with db.session(staff.clinic_id) as s:
        c = s.get(Call, call_id)
        if c is None or c.clinic_id != staff.clinic_id:
            raise HTTPException(404)
        return {"live": False, "id": c.id, "summary": c.summary, "transcript": c.transcript, "events": c.events,
                "outcome": c.outcome, "failure_reasons": c.failure_reasons, "latency_ms": c.latency_ms}


class CorrectionIn(BaseModel):
    note: str


@app.post("/api/calls/{call_id}/correction")
def staff_correction(call_id: str, body: CorrectionIn, staff: Staff = Depends(current_staff)):
    """Staff flag that the AI got something wrong. Feeds the improvement loop."""
    with db.session(staff.clinic_id) as s:
        c = s.get(Call, call_id)
        if c is None or c.clinic_id != staff.clinic_id:
            raise HTTPException(404)
        c.staff_correction = body.note[:2000]
        c.failure_reasons = sorted(set(c.failure_reasons or []) | {"staff_correction"})
    return {"ok": True}


# ------------------------------------------------------------ messages, bookings, reports


@app.get("/api/messages")
def messages(open_only: bool = True, staff: Staff = Depends(current_staff)):
    with db.session(staff.clinic_id) as s:
        q = select(Message).where(Message.clinic_id == staff.clinic_id)
        if open_only:
            q = q.where(Message.handled.is_(False))
        rows = s.scalars(q.order_by(Message.urgent.desc(), Message.created_at.desc()).limit(200))
        return [{"id": m.id, "caller_name": m.caller_name, "caller_phone": m.caller_phone, "body": m.body,
                 "urgent": m.urgent, "handled": m.handled, "created_at": m.created_at, "call_id": m.call_id} for m in rows]


@app.post("/api/messages/{message_id}/handled")
def message_handled(message_id: int, staff: Staff = Depends(current_staff)):
    with db.session(staff.clinic_id) as s:
        m = s.get(Message, message_id)
        if m is None or m.clinic_id != staff.clinic_id:
            raise HTTPException(404)
        m.handled = True
    return {"ok": True}


@app.get("/api/bookings")
def bookings(ai_only: bool = True, days: int = 14, staff: Staff = Depends(current_staff)):
    with db.session(staff.clinic_id) as s:
        q = select(Booking, Patient, Practitioner).join(Patient, Booking.patient_id == Patient.id).join(
            Practitioner, Booking.practitioner_id == Practitioner.id).where(
            Booking.clinic_id == staff.clinic_id, Booking.updated_at >= clock.now() - timedelta(days=days))
        if ai_only:
            q = q.where(Booking.call_id.is_not(None))
        return [{"id": b.id, "patient": p.full_name, "practitioner": pr.name, "start": b.start, "status": b.status,
                 "created_by": b.created_by, "call_id": b.call_id, "updated_at": b.updated_at}
                for b, p, pr in s.execute(q.order_by(Booking.updated_at.desc()).limit(300))]


@app.get("/api/reports")
def reports(days: int = 30, staff: Staff = Depends(owner_only)):
    since = clock.now() - timedelta(days=days)
    with db.session(staff.clinic_id) as s:
        calls = list(s.scalars(select(Call).where(Call.clinic_id == staff.clinic_id, Call.started_at >= since)))
        ai_bookings = s.scalar(select(func.count()).select_from(Booking).where(
            Booking.clinic_id == staff.clinic_id, Booking.call_id.is_not(None), Booking.created_at >= since))
    finished = [c for c in calls if c.ended_at]
    resolved = [c for c in finished if not c.failure_reasons and c.outcome not in (None, "no_action")]
    minutes = sum((c.ended_at - c.started_at).total_seconds() for c in finished) / 60
    by_day: dict[str, int] = defaultdict(int)
    for c in calls:
        by_day[c.started_at.date().isoformat()] += 1
    return {
        "days": days, "calls_answered": len(calls), "bookings_by_ai": ai_bookings,
        "resolved_without_staff_pct": round(100 * len(resolved) / len(finished), 1) if finished else None,
        "staff_minutes_saved": round(minutes, 1),
        "outcomes": {o: sum(1 for c in finished if c.outcome == o) for o in sorted({c.outcome for c in finished if c.outcome})},
        "calls_by_day": dict(sorted(by_day.items())),
    }


class AIToggle(BaseModel):
    enabled: bool


@app.post("/api/clinic/ai")
def toggle_ai(body: AIToggle, staff: Staff = Depends(owner_only)):
    """Kill switch: the clinic can turn the AI off instantly."""
    with db.session(staff.clinic_id) as s:
        s.get(Clinic, staff.clinic_id).ai_enabled = body.enabled
    return {"ai_enabled": body.enabled}


@app.delete("/api/patients/{patient_id}")
def erase_patient(patient_id: int, staff: Staff = Depends(owner_only)):
    """Right to erasure, actioned by the clinic (the data controller)."""
    with db.session(staff.clinic_id) as s:
        forget_patient(s, staff.clinic_id, patient_id)
    return {"ok": True}


# ------------------------------------------------------------ text simulator (staging / load tests)


class SimStart(BaseModel):
    caller_phone: str | None = None


class SimTurn(BaseModel):
    text: str


def _sim_enabled() -> None:
    if not settings.enable_sim_api or settings.env == "production":
        raise HTTPException(404)


@app.post("/api/sim/calls", dependencies=[Depends(_sim_enabled)])
def sim_start(body: SimStart, staff: Staff = Depends(current_staff)):
    call_id = f"sim-{uuid.uuid4().hex[:16]}"
    config, arm, shadow = rollout.choose(call_id)
    session = CallSession(call_id, staff.clinic_id, body.caller_phone, config=config, rollout_arm=arm, shadow=shadow)
    greeting = session.start()
    return {"call_id": call_id, "reply": greeting}


@app.post("/api/sim/calls/{call_id}/turn", dependencies=[Depends(_sim_enabled)])
async def sim_turn(call_id: str, body: SimTurn, staff: Staff = Depends(current_staff)):
    session = registry.get(call_id)
    if session is None or session.clinic_id != staff.clinic_id:
        raise HTTPException(404, "no such live call")
    result = await asyncio.to_thread(session.turn, body.text)
    if result.ended:
        await asyncio.to_thread(session.end, "completed")
    return {"reply": result.reply, "ended": result.ended, "latency_ms": round(result.latency_ms, 1)}


@app.post("/api/sim/calls/{call_id}/end", dependencies=[Depends(_sim_enabled)])
async def sim_end(call_id: str, staff: Staff = Depends(current_staff)):
    session = registry.get(call_id)
    if session is not None and session.clinic_id == staff.clinic_id:
        await asyncio.to_thread(session.end, "hangup")
    return {"ok": True}


@app.get("/api/me")
def me(staff: Staff = Depends(current_staff)):
    with db.session(staff.clinic_id) as s:
        clinic = s.get(Clinic, staff.clinic_id)
        return {"email": staff.email, "role": staff.role,
                "clinic": {"id": clinic.id, "name": clinic.name, "ai_enabled": clinic.ai_enabled},
                "server_time": datetime.now().isoformat()}
