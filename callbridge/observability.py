"""Metrics (Prometheus -> Grafana), errors (Sentry), traces (Langfuse), PII-safe logging, cost cap.
Everything sent off-box goes through safety.pii.redact first."""

import logging
import threading
from contextvars import ContextVar
from datetime import date

from prometheus_client import Counter, Gauge, Histogram

from callbridge.config import get_settings
from callbridge.safety.pii import RedactingFilter, redact

current_call_id: ContextVar[str | None] = ContextVar("current_call_id", default=None)

CALLS = Counter("callbridge_calls_total", "Calls started", ["clinic"])
CALL_OUTCOMES = Counter("callbridge_call_outcomes_total", "Calls by outcome", ["clinic", "outcome"])
ACTIVE_CALLS = Gauge("callbridge_active_calls", "Calls in progress")
TURN_LATENCY = Histogram("callbridge_turn_latency_seconds", "Caller stops -> reply ready (agent side)",
                         buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2, 3, 5))
VOICE_LATENCY = Histogram("callbridge_voice_response_seconds", "Caller stops speaking -> first bot audio",
                          buckets=(0.2, 0.4, 0.6, 0.8, 1.0, 1.4, 2, 3, 5))
LLM_CALLS = Counter("callbridge_llm_calls_total", "LLM calls", ["provider", "tier"])
LLM_FAILURES = Counter("callbridge_llm_failures_total", "LLM failures", ["provider"])
COST = Counter("callbridge_cost_gbp_total", "Estimated provider cost in GBP")
HANDOFFS = Counter("callbridge_handoffs_total", "Calls passed to humans", ["reason"])
REDFLAGS = Counter("callbridge_redflags_total", "Red-flag escalations", ["category"])
SAFETY_BLOCKS = Counter("callbridge_safety_blocks_total", "Proposals rejected by the checker", ["reason"])

_lock = threading.Lock()
_spend: dict[date, float] = {}
_langfuse = None


def setup() -> None:
    st = get_settings()
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for h in root.handlers:
        h.addFilter(RedactingFilter())
    if st.sentry_dsn:
        import sentry_sdk

        def _scrub(event, _hint):
            for exc in (event.get("exception") or {}).get("values", []):
                exc["value"] = redact(exc.get("value") or "")
            return event

        sentry_sdk.init(dsn=st.sentry_dsn, environment=st.env, send_default_pii=False, before_send=_scrub,
                        traces_sample_rate=0.1)
    global _langfuse
    if st.langfuse_enabled:
        try:
            from langfuse import get_client

            _langfuse = get_client()
        except Exception:
            logging.getLogger(__name__).exception("langfuse init failed")


def add_cost(gbp: float) -> float:
    """Track today's spend; returns the running total. Callers check over_budget()."""
    from callbridge import clock

    with _lock:
        today = clock.now().date()
        _spend[today] = _spend.get(today, 0.0) + gbp
        COST.inc(gbp)
        return _spend[today]


def over_budget() -> bool:
    from callbridge import clock

    return _spend.get(clock.now().date(), 0.0) >= get_settings().daily_cost_cap_gbp


def record_llm_call(*, provider: str, model: str, latency_ms: float, cost_gbp: float, input: str, output) -> None:
    LLM_CALLS.labels(provider, "smart" if provider in ("anthropic", "openai") else "fast").inc()
    add_cost(cost_gbp)
    if _langfuse is None:
        return
    try:
        obs = _langfuse.start_observation(
            name="nlu", as_type="generation", model=model, input=redact(input), output=redact(str(output)),
            metadata={"call_id": current_call_id.get(), "provider": provider, "latency_ms": round(latency_ms)},
            cost_details={"total": cost_gbp},
        )
        obs.end()
    except Exception:
        logging.getLogger(__name__).debug("langfuse record failed", exc_info=True)


def record_turn(*, call_id: str, node_path: list[str], latency_ms: float, user_text: str, reply: str) -> None:
    TURN_LATENCY.observe(latency_ms / 1000)
    if _langfuse is None:
        return
    try:
        obs = _langfuse.start_observation(name="turn", as_type="chain", input=redact(user_text), output=redact(reply),
                                          metadata={"call_id": call_id, "path": node_path, "latency_ms": round(latency_ms)})
        obs.end()
    except Exception:
        logging.getLogger(__name__).debug("langfuse record failed", exc_info=True)
