"""Model router + fallback chain for the understanding step.

  easy turn ("yes", "no thanks", "bye")  -> fast path: Groq small model if configured, else RuleNLU
  everything else                        -> Claude -> GPT (each behind a circuit breaker + timeout)
  all providers down                     -> LLMUnavailable; the call switches to taking a message

LLMs return *phrases* for dates/times; agent.dates resolves them deterministically."""

import json
import logging
import time
from dataclasses import dataclass

from callbridge.agent import dates
from callbridge.agent.nlu import EASY_INTENTS, INTENTS, NLUContext, NLUResult, RuleNLU
from callbridge.config import Settings, get_settings
from callbridge.observability import LLM_FAILURES, record_llm_call
from callbridge.resilience import CircuitBreaker, with_timeout

log = logging.getLogger(__name__)

USD_TO_GBP = 0.79
# USD per million tokens (input, output). Check provider pricing pages before relying on these.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
    "gpt-5": (1.25, 10.0), "llama-3.1-8b-instant": (0.05, 0.08),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "first_name": {"type": "string"}, "last_name": {"type": "string"},
        "dob_phrase": {"type": "string"}, "day_phrase": {"type": "string"}, "time_phrase": {"type": "string"},
        "daypart": {"type": "string", "enum": ["", "morning", "afternoon", "evening"]},
        "practitioner": {"type": "string"},
        "service": {"type": "string", "enum": ["", "physio", "osteopathy", "sports massage"]},
        "choice": {"type": "integer"}, "any_time": {"type": "boolean"},
        "question": {"type": "string"}, "confidence": {"type": "number"},
    },
    "required": ["intent", "first_name", "last_name", "dob_phrase", "day_phrase", "time_phrase", "daypart",
                 "practitioner", "service", "choice", "any_time", "question", "confidence"],
    "additionalProperties": False,
}


class LLMUnavailable(Exception):
    pass


@dataclass
class TurnMeta:
    tier: str = "rules"
    model: str = "rules"
    latency_ms: float = 0.0
    cost_gbp: float = 0.0
    fallback_used: bool = False


def cost_gbp(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout) / 1_000_000 * USD_TO_GBP


def user_payload(text: str, ctx: NLUContext) -> str:
    return json.dumps({
        "today": f"{ctx.today.isoformat()} ({ctx.today.strftime('%A')})",
        "assistant_just_said": ctx.last_bot_utterance,
        "waiting_for": ctx.awaiting or "",
        "options_offered": ctx.offered_count,
        "practitioners": ctx.practitioner_names,
        "caller_utterance": text,
    })


def resolve(raw: dict, ctx: NLUContext, source: str) -> NLUResult:
    """Turn the LLM's phrases into concrete values with the deterministic resolvers."""
    choice = int(raw.get("choice") or 0)
    if choice and ctx.offered_count and choice >= ctx.offered_count:
        choice = -1
    intent = raw.get("intent") if raw.get("intent") in INTENTS else "unknown"
    return NLUResult(
        intent=intent,
        first_name=(raw.get("first_name") or "").strip().title() or None,
        last_name=(raw.get("last_name") or "").strip().title() or None,
        dob=dates.parse_full_date(raw.get("dob_phrase") or ""),
        day=dates.parse_day(raw.get("day_phrase") or "", ctx.today, ctx.config.next_weekday_policy),
        time=dates.parse_time(raw.get("time_phrase") or ""),
        daypart=raw.get("daypart") or None,
        practitioner=next((n for n in ctx.practitioner_names if n.lower() == (raw.get("practitioner") or "").lower()), None),
        service=raw.get("service") or None,
        choice=choice or None,
        any_time=bool(raw.get("any_time")),
        question=raw.get("question") or None,
        confidence=float(raw.get("confidence") or 0.5),
        source=source,
    )


class AnthropicNLU:
    name = "anthropic"

    def __init__(self, st: Settings):
        import anthropic

        self.model = st.anthropic_model
        self.effort = st.anthropic_effort
        # Retries are handled by the fallback chain; one slow retry would blow the latency budget.
        self.client = anthropic.Anthropic(api_key=st.anthropic_api_key, max_retries=0, timeout=st.llm_timeout_s)

    def extract(self, text: str, ctx: NLUContext) -> tuple[dict, float]:
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=2048,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            # Stable instructions first and cached; the per-turn payload goes last.
            system=[{"type": "text", "text": ctx.config.nlu_instructions, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_payload(text, ctx)}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused")
        body = next(b.text for b in resp.content if b.type == "text")
        return json.loads(body), cost_gbp(self.model, resp.usage.input_tokens, resp.usage.output_tokens)


class OpenAICompatibleNLU:
    """GPT (backup for hard turns) and Groq (fast tier) both speak the OpenAI chat API."""

    def __init__(self, name: str, model: str, api_key: str, timeout: float, base_url: str | None = None, strict: bool = True):
        import openai

        self.name, self.model, self.strict = name, model, strict
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)

    def extract(self, text: str, ctx: NLUContext) -> tuple[dict, float]:
        fmt = ({"type": "json_schema", "json_schema": {"name": "nlu", "schema": SCHEMA, "strict": True}} if self.strict
               else {"type": "json_object"})
        system = ctx.config.nlu_instructions + ("" if self.strict else "\nReturn a JSON object with keys: " + ", ".join(SCHEMA["required"]))
        resp = self.client.chat.completions.create(
            model=self.model, response_format=fmt,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_payload(text, ctx)}],
        )
        usage = resp.usage
        return json.loads(resp.choices[0].message.content or "{}"), cost_gbp(
            self.model, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0)


class NLUEngine:
    def __init__(self, settings: Settings | None = None):
        st = settings or get_settings()
        self.settings = st
        self.rules = RuleNLU()
        self.fast = None
        self.chain: list = []
        if st.llm_provider != "rules":
            if st.groq_api_key:
                self.fast = OpenAICompatibleNLU("groq", st.groq_model, st.groq_api_key, st.llm_timeout_s,
                                                base_url="https://api.groq.com/openai/v1", strict=False)
            if st.anthropic_api_key:
                self.chain.append(AnthropicNLU(st))
            if st.openai_api_key:
                self.chain.append(OpenAICompatibleNLU("openai", st.openai_model, st.openai_api_key, st.llm_timeout_s))
        self.breakers = {p.name: CircuitBreaker(p.name) for p in self.chain + ([self.fast] if self.fast else [])}

    @property
    def offline(self) -> bool:
        return not self.chain

    def parse(self, text: str, ctx: NLUContext) -> tuple[NLUResult, TurnMeta]:
        t0 = time.perf_counter()
        quick = self.rules.parse(text, ctx)
        if self.offline:
            return quick, TurnMeta(latency_ms=(time.perf_counter() - t0) * 1000)

        if quick.intent in EASY_INTENTS and quick.confidence >= 0.9:
            if self.fast is None:
                return quick, TurnMeta(tier="fast", latency_ms=(time.perf_counter() - t0) * 1000)
            try:
                return self._call(self.fast, text, ctx, t0, tier="fast", fallback=False)
            except Exception:
                return quick, TurnMeta(tier="fast", latency_ms=(time.perf_counter() - t0) * 1000, fallback_used=True)

        for i, provider in enumerate(self.chain):
            try:
                return self._call(provider, text, ctx, t0, tier="smart", fallback=i > 0)
            except Exception as e:  # timeout, 5xx, circuit open, bad JSON: try the next provider
                LLM_FAILURES.labels(provider.name).inc()
                log.warning("nlu provider %s failed: %s", provider.name, type(e).__name__)
        raise LLMUnavailable("all language providers failed")

    def _call(self, provider, text: str, ctx: NLUContext, t0: float, tier: str, fallback: bool) -> tuple[NLUResult, TurnMeta]:
        breaker = self.breakers[provider.name]
        raw, cost = breaker.call(lambda: with_timeout(lambda: provider.extract(text, ctx), self.settings.llm_timeout_s))
        result = resolve(raw, ctx, provider.name)
        meta = TurnMeta(tier=tier, model=provider.model, latency_ms=(time.perf_counter() - t0) * 1000, cost_gbp=cost,
                        fallback_used=fallback)
        record_llm_call(provider=provider.name, model=provider.model, latency_ms=meta.latency_ms, cost_gbp=cost,
                        input=text, output=raw)
        return result, meta
