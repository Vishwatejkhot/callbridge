"""One phone call: holds the conversation state, runs a graph turn per caller utterance, persists a
redacted record, feeds the dashboard and the improvement loop. Transport-agnostic: the Pipecat
voice pipeline, the text simulator API, the CLI and the eval harness all drive this same class."""

import logging
import threading
import time
from dataclasses import dataclass

from callbridge import clock, db
from callbridge.agent.graph import GRAPH, CallState, Deps, new_state, nlu_context, summarize
from callbridge.agent.llm import NLUEngine
from callbridge.agent.nlu import normalise
from callbridge.agent.prompts import PromptConfig
from callbridge.calls import registry
from callbridge.db.models import Call, Clinic
from callbridge.observability import ACTIVE_CALLS, CALL_OUTCOMES, CALLS, current_call_id, over_budget, record_turn
from callbridge.safety.pii import redact
from callbridge.tools import booking, identity

log = logging.getLogger(__name__)

SAFE_FALLBACK = "Sorry, I'm having a little trouble. Let me take a message and someone will call you back. What would you like me to pass on?"


@dataclass
class TurnResult:
    reply: str
    ended: bool
    transfer: bool
    latency_ms: float


class CallSession:
    def __init__(self, call_id: str, clinic_id: int, caller_phone: str | None, *, engine: NLUEngine | None = None,
                 config: PromptConfig | None = None, rollout_arm: str = "live", persist: bool = True,
                 shadow=None):
        self.call_id, self.clinic_id, self.caller_phone = call_id, clinic_id, caller_phone
        self.engine = engine or NLUEngine()
        self.config = config or PromptConfig()
        self.rollout_arm = rollout_arm
        self.persist = persist
        self.shadow = shadow  # improver.rollout.ShadowRunner or None
        self.state: CallState | None = None
        self.transcript: list[dict] = []
        self.latencies: list[float] = []
        self.cost_gbp = 0.0
        self.taken_over = False
        self._lock = threading.Lock()
        self._practitioners: list[dict] = []
        self._prefetched: dict[tuple, tuple] = {}

    @property
    def practitioners(self) -> list[dict]:
        return self._practitioners

    # ------------------------------------------------------------ lifecycle

    def start(self) -> str:
        with db.session(self.clinic_id) as s:
            clinic = s.get(Clinic, self.clinic_id)
            assert clinic is not None
            recognised = identity.recognise_by_phone(s, self.clinic_id, self.caller_phone)
            self.state = new_state(self.call_id, clinic, self.caller_phone, recognised.id if recognised else None)
            self._practitioners = [{"id": p.id, "name": p.name, "first": p.name.split()[0], "service": p.service}
                                   for p in booking.practitioners(s, self.clinic_id)]
            greeting = self.config.greeting.format(clinic=clinic.name)
            if self.persist:
                s.merge(Call(id=self.call_id, clinic_id=self.clinic_id, caller_phone=self.caller_phone,
                             patient_id=None, prompt_version=self.config.version, rollout_arm=self.rollout_arm,
                             started_at=clock.now()))
        self.state["reply"] = greeting
        self.state["last_question"] = "How can I help?"
        self._log("assistant", greeting)
        CALLS.labels(str(self.clinic_id)).inc()
        ACTIVE_CALLS.inc()
        registry.register(self)
        return greeting

    def turn(self, text: str) -> TurnResult:
        assert self.state is not None, "call start() first"
        with self._lock:
            t0 = time.perf_counter()
            token = current_call_id.set(self.call_id)
            self._log("caller", text)
            prior_awaiting = self.state.get("awaiting")
            try:
                if self.taken_over:
                    return TurnResult("", ended=True, transfer=False, latency_ms=0)
                if over_budget() and not self.engine.offline:
                    self.engine = NLUEngine(self.engine_settings_offline())  # daily cost cap hit: degrade to offline parsing
                deps = Deps(s=None, engine=self.engine, config=self.config, practitioners=self._practitioners,  # type: ignore[arg-type]
                            prefetched=self._prefetched.pop((normalise(text).lower(), prior_awaiting), None))
                self._prefetched.clear()
                with db.session(self.clinic_id) as s:
                    deps.s = s
                    out = GRAPH.invoke({**self.state, "user_text": text, "path": []},
                                       config={"configurable": {"deps": deps}, "recursion_limit": 30})
                self.state = out
                self.transcript[-1]["intent"] = (out.get("nlu") or {}).get("intent") if "understand" in out.get("path", []) else None
                self.cost_gbp += deps.turn_meta.get("cost_gbp", 0.0)
            except Exception:
                log.exception("turn failed")
                self.state["awaiting"] = "message_body"
                self.state["reply"] = SAFE_FALLBACK
                self.state["events"] = [*self.state.get("events", []), "turn_error"]
            finally:
                current_call_id.reset(token)
            latency = (time.perf_counter() - t0) * 1000
            self.latencies.append(latency)
            reply = self.state["reply"]
            self._log("assistant", reply)
            record_turn(call_id=self.call_id, node_path=self.state.get("path", []), latency_ms=latency, user_text=text, reply=reply)
            if self.shadow is not None:
                self.shadow.compare(text, self.state.get("nlu", {}), prior_awaiting, self._practitioners)
            registry.publish({"type": "turn", "call_id": self.call_id, "clinic_id": self.clinic_id,
                              "confidence": self.state.get("confidence"), "summary": summarize(self.state),
                              "alert": self.needs_attention})
            ended = self.state.get("status") in ("ended", "handoff")
            return TurnResult(reply, ended=ended, transfer=bool(self.state.get("transfer")), latency_ms=latency)

    def prefetch(self, text: str) -> None:
        """Speculatively understand a partial utterance while the caller is still speaking. If the final
        utterance turns out identical, the turn skips the LLM round trip; otherwise the draft is ignored."""
        st = self.state
        if st is None or self.engine.offline or self._lock.locked():
            return
        key = (normalise(text).lower(), st.get("awaiting"))
        try:
            result = self.engine.parse(text, nlu_context(st, self.config, self._practitioners))
        except Exception:
            return
        self._prefetched = {key: result}

    def end(self, reason: str = "hangup") -> None:
        if self.state is None:
            return
        outcome, failures = self.outcome(reason)
        CALL_OUTCOMES.labels(str(self.clinic_id), outcome).inc()
        ACTIVE_CALLS.dec()
        registry.unregister(self.call_id)
        if self.persist:
            with db.session(self.clinic_id) as s:
                call = s.get(Call, self.call_id)
                if call is not None:
                    call.ended_at = clock.now()
                    call.status = "taken_over" if self.taken_over else "ended"
                    call.outcome = outcome
                    call.summary = redact(summarize(self.state), self._known_names())
                    call.confidence = self.state.get("confidence", 1.0)
                    call.patient_id = self.state.get("verified_patient_id")
                    call.transcript = [{**t, "text": redact(t["text"], self._known_names())} for t in self.transcript]
                    call.events = self.state.get("events", [])
                    call.latency_ms = [round(x, 1) for x in self.latencies]
                    call.cost_gbp = round(self.cost_gbp, 5)
                    call.failure_reasons = failures
        from callbridge.improver import rollout

        rollout.on_call_finished(self.rollout_arm, self.config.version, ok=not failures)

    # ------------------------------------------------------------ views

    def outcome(self, reason: str = "hangup") -> tuple[str, list[str]]:
        st = self.state or {}
        events, flags = st.get("events", []), st.get("flags", [])
        outcome = "no_action"
        for e in ("booked", "rescheduled", "cancelled", "forgotten"):
            if e in events:
                outcome = e
                break
        else:
            if self.taken_over:
                outcome = "taken_over"
            elif "handoff" in events:
                outcome = "handoff"
            elif "message_taken" in events:
                outcome = "message"
            elif any(e.startswith("redflag") for e in events):
                outcome = "escalated"
            elif "faq_answered" in events:
                outcome = "answered"
        failures: list[str] = []
        if reason == "hangup" and st.get("task"):
            failures.append("hangup_mid_task")
        if "action_blocked" in flags:
            failures.append("action_blocked")
        if any(e in ("handoff", "handoff_unavailable") for e in events) or self.taken_over:
            failures.append("handoff")
        if "low_confidence" in flags:
            failures.append("low_confidence")
        if "verify_failed" in flags:
            failures.append("verify_failed")
        if "llm_down" in events or "turn_error" in events:
            failures.append("system_error")
        if "faq_unknown" in events:
            failures.append("faq_unknown")
        if "not_understood" in events:  # even one is worth learning from
            failures.append("misunderstanding")
        return outcome, failures

    @property
    def needs_attention(self) -> bool:
        st = self.state or {}
        return st.get("confidence", 1.0) < registry.LOW_CONFIDENCE_ALERT or bool(
            {"redflag", "low_confidence", "action_blocked"} & set(st.get("flags", [])))

    def snapshot(self) -> dict:
        st = self.state or {}
        return {
            "call_id": self.call_id, "clinic_id": self.clinic_id, "caller_phone": self.caller_phone,
            "summary": summarize(st) if st else "", "confidence": st.get("confidence", 1.0),
            "alert": self.needs_attention, "status": st.get("status"), "task": st.get("task"),
            "transcript": self.transcript, "flags": st.get("flags", []),
        }

    def mark_taken_over(self) -> None:
        self.taken_over = True
        if self.state is not None:
            self.state["status"] = "handoff"

    # ------------------------------------------------------------ internals

    def _log(self, role: str, text: str) -> None:
        self.transcript.append({"role": role, "text": text, "at": clock.now().isoformat()})

    def _known_names(self) -> list[str]:
        st = self.state or {}
        names = [st.get("verified_name") or ""]
        ident = st.get("identity") or {}
        names += [ident.get("first_name") or "", ident.get("last_name") or ""]
        return [n for n in names if n]

    @staticmethod
    def engine_settings_offline():
        from callbridge.config import get_settings

        return get_settings().model_copy(update={"llm_provider": "rules"})
