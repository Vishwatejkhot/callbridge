"""Safe rollout of behaviour-config versions: gated -> shadow -> (human approval) -> canary 10% -> live,
with automatic rollback when the numbers drop.

Status machine for PromptVersion:
  draft --eval gate--> gated --start_shadow--> shadow --approve(human)--> canary --evaluate--> live
                 +-> rejected                                              +-> rolled_back
  live --evaluate (regression vs parent)--> rolled_back (parent restored)"""

import hashlib
import threading
import time

from sqlalchemy import select

from callbridge import db
from callbridge.agent.nlu import NLUContext, RuleNLU
from callbridge.agent.prompts import PromptConfig
from callbridge.db.models import PromptVersion
from callbridge import clock

CANARY_PERCENT = 10
MIN_SHADOW_TURNS = 200
MIN_SHADOW_AGREEMENT = 0.9
MIN_CANARY_CALLS = 50
MAX_SUCCESS_DROP = 0.05  # absolute drop in call success rate that triggers rollback

_lock = threading.Lock()
_cache: tuple[float, PromptConfig, PromptConfig | None, PromptConfig | None] | None = None
_counters: dict[tuple[str, str], list[int]] = {}  # (version, arm) -> [calls, ok]


class RolloutError(Exception):
    pass


def _configs() -> tuple[PromptConfig, PromptConfig | None, PromptConfig | None]:
    """(live, canary, shadow) configs, cached for 30s."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < 30:
        return _cache[1], _cache[2], _cache[3]
    live = PromptConfig()
    canary = shadow = None
    try:
        with db.session() as s:
            for v in s.scalars(select(PromptVersion).where(PromptVersion.status.in_(["live", "canary", "shadow"]))):
                cfg = PromptConfig.model_validate({**v.config, "version": v.version})
                if v.status == "live":
                    live = cfg
                elif v.status == "canary":
                    canary = cfg
                else:
                    shadow = cfg
    except Exception:  # never let rollout bookkeeping stop a call from being answered
        pass
    _cache = (time.monotonic(), live, canary, shadow)
    return live, canary, shadow


def invalidate() -> None:
    global _cache
    _cache = None


def choose(call_id: str) -> tuple[PromptConfig, str, "ShadowRunner | None"]:
    live, canary, shadow = _configs()
    bucket = int(hashlib.sha256(call_id.encode()).hexdigest(), 16) % 100
    if canary is not None and bucket < CANARY_PERCENT:
        return canary, "canary", None
    return live, "live", ShadowRunner(shadow) if shadow is not None else None


class ShadowRunner:
    """Runs the candidate's understanding step silently on live turns and records agreement.
    The candidate never speaks to the caller and never touches data."""

    def __init__(self, config: PromptConfig, engine=None):
        self.config = config
        self.engine = engine  # same NLUEngine as live (LLM mode); None = rules

    def compare(self, text: str, live_nlu: dict, awaiting: str | None, practitioners: list[dict]) -> None:
        ctx = NLUContext(today=clock.now().date(), awaiting=awaiting, practitioner_names=[p["first"] for p in practitioners],
                         config=self.config)
        try:
            result = self.engine.parse(text, ctx)[0] if self.engine is not None else RuleNLU().parse(text, ctx)
        except Exception:
            return
        cand = result.model_dump(mode="json")
        keys = ("intent", "day", "time", "dob", "choice", "practitioner")
        agree = all(cand.get(k) == live_nlu.get(k) for k in keys)
        record_shadow(self.config.version, agree)


def record_shadow(version: str, agree: bool) -> None:
    with _lock:
        c = _counters.setdefault((version, "shadow"), [0, 0])
        c[0] += 1
        c[1] += int(agree)


def on_call_finished(arm: str, version: str, ok: bool) -> None:
    with _lock:
        c = _counters.setdefault((version, arm), [0, 0])
        c[0] += 1
        c[1] += int(ok)


def flush_counters() -> None:
    """Persist in-memory counters (call from a periodic job, or before evaluate)."""
    with _lock:
        pending = dict(_counters)
        _counters.clear()
    if not pending:
        return
    with db.session() as s:
        for (version, arm), (n, ok) in pending.items():
            v = s.scalar(select(PromptVersion).where(PromptVersion.version == version))
            if v is None:
                continue
            stats = dict(v.shadow_stats or {})
            cur = stats.get(arm, {"n": 0, "ok": 0})
            stats[arm] = {"n": cur["n"] + n, "ok": cur["ok"] + ok}
            v.shadow_stats = stats


def _rate(stats: dict, arm: str) -> tuple[int, float]:
    a = stats.get(arm) or {"n": 0, "ok": 0}
    return a["n"], (a["ok"] / a["n"]) if a["n"] else 0.0


# ------------------------------------------------------------ transitions


def submit(config: PromptConfig, parent: str | None, rationale: str) -> str:
    with db.session() as s:
        version = config.version
        s.add(PromptVersion(version=version, parent=parent, config=config.model_dump(exclude={"version"}),
                            rationale=rationale, status="draft"))
    return version


def record_gate(version: str, report: dict, passed: bool) -> None:
    with db.session() as s:
        v = _get(s, version)
        v.eval_report = report
        v.status = "gated" if passed else "rejected"


def start_shadow(version: str) -> None:
    with db.session() as s:
        v = _get(s, version)
        if v.status != "gated":
            raise RolloutError(f"{version} must pass the eval gate first (status={v.status})")
        for other in s.scalars(select(PromptVersion).where(PromptVersion.status == "shadow")):
            other.status = "gated"
        v.status = "shadow"
    invalidate()


def approve(version: str, approved_by: str, *, require_shadow: bool = True) -> None:
    """Human approval: the only way anything reaches real callers."""
    flush_counters()
    with db.session() as s:
        v = _get(s, version)
        if v.status not in ("shadow", "gated"):
            raise RolloutError(f"{version} is {v.status}; only gated/shadow versions can be approved")
        n, agreement = _rate(v.shadow_stats or {}, "shadow")
        if require_shadow and (n < MIN_SHADOW_TURNS or agreement < MIN_SHADOW_AGREEMENT):
            raise RolloutError(f"shadow evidence too weak: {n} turns, {agreement:.0%} agreement "
                               f"(need {MIN_SHADOW_TURNS} turns at {MIN_SHADOW_AGREEMENT:.0%})")
        if s.scalar(select(PromptVersion).where(PromptVersion.status == "canary")):
            raise RolloutError("another canary is already running")
        v.status, v.approved_by = "canary", approved_by
    invalidate()


def evaluate() -> list[str]:
    """Promote or roll back based on live numbers. Run every few minutes. Returns actions taken."""
    flush_counters()
    actions: list[str] = []
    with db.session() as s:
        live = s.scalar(select(PromptVersion).where(PromptVersion.status == "live"))
        live_n, live_rate = _rate(live.shadow_stats or {}, "live") if live else (0, 0.0)
        canary = s.scalar(select(PromptVersion).where(PromptVersion.status == "canary"))
        if canary is not None:
            n, rate = _rate(canary.shadow_stats or {}, "canary")
            if n >= MIN_CANARY_CALLS:
                baseline = live_rate if live_n >= MIN_CANARY_CALLS else None
                if baseline is not None and rate < baseline - MAX_SUCCESS_DROP:
                    canary.status = "rolled_back"
                    actions.append(f"rolled back canary {canary.version}: {rate:.1%} vs live {baseline:.1%}")
                else:
                    if live is not None:
                        live.status = "retired"
                    canary.status = "live"
                    actions.append(f"promoted {canary.version} to 100%")
        elif live is not None and live.parent:
            parent = s.scalar(select(PromptVersion).where(PromptVersion.version == live.parent))
            if parent is not None and live_n >= MIN_CANARY_CALLS:
                p_n, p_rate = _rate(parent.shadow_stats or {}, "live")
                if p_n >= MIN_CANARY_CALLS and live_rate < p_rate - MAX_SUCCESS_DROP:
                    live.status, parent.status = "rolled_back", "live"
                    actions.append(f"auto-rollback {live.version} -> {parent.version}: {live_rate:.1%} vs {p_rate:.1%}")
    if actions:
        invalidate()
    return actions


def rollback(version: str) -> None:
    with db.session() as s:
        v = _get(s, version)
        parent = s.scalar(select(PromptVersion).where(PromptVersion.version == v.parent)) if v.parent else None
        v.status = "rolled_back"
        if parent is not None and not s.scalar(select(PromptVersion).where(PromptVersion.status == "live")):
            parent.status = "live"
    invalidate()


def _get(s, version: str) -> PromptVersion:
    v = s.scalar(select(PromptVersion).where(PromptVersion.version == version))
    if v is None:
        raise RolloutError(f"unknown version {version}")
    return v
