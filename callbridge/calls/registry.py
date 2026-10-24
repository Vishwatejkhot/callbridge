"""Live calls in this process, for the staff dashboard. Voice calls are pinned to one instance by
their websocket, so in-memory is correct here; the database holds the durable record."""

import asyncio
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from callbridge.agent.session import CallSession

LOW_CONFIDENCE_ALERT = 0.55

_lock = threading.Lock()
_calls: dict[str, "CallSession"] = {}
_takeover_handlers: dict[str, Callable[[], None]] = {}
_subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []


def register(session: "CallSession") -> None:
    with _lock:
        _calls[session.call_id] = session
    publish({"type": "call_started", "call_id": session.call_id, "clinic_id": session.clinic_id})


def unregister(call_id: str) -> None:
    with _lock:
        _calls.pop(call_id, None)
        _takeover_handlers.pop(call_id, None)
    publish({"type": "call_ended", "call_id": call_id})


def get(call_id: str) -> "CallSession | None":
    return _calls.get(call_id)


def live(clinic_id: int) -> list["CallSession"]:
    with _lock:
        return [c for c in _calls.values() if c.clinic_id == clinic_id]


def on_takeover(call_id: str, handler: Callable[[], None]) -> None:
    """The voice pipeline registers how to hand this call to a human (Twilio redirect)."""
    _takeover_handlers[call_id] = handler


def takeover_handler(call_id: str) -> Callable[[], None] | None:
    return _takeover_handlers.get(call_id)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append((asyncio.get_running_loop(), q))
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    for item in list(_subscribers):
        if item[1] is q:
            _subscribers.remove(item)


def publish(event: dict) -> None:
    """Thread-safe fan-out to dashboard websocket/SSE subscribers."""
    for loop, q in list(_subscribers):
        try:
            loop.call_soon_threadsafe(_put, q, event)
        except RuntimeError:
            unsubscribe(q)


def _put(q: asyncio.Queue, event: dict) -> None:
    if not q.full():
        q.put_nowait(event)
