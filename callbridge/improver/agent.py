"""The improver: reads recent failures and proposes a change to the behaviour config.

Its edit surface is deliberately small: extra phrases per intent, and an appended note to the
understanding instructions. It cannot touch code, the safety checker, tools or permissions, and
nothing it proposes reaches callers without passing every test, shadow mode and a human."""

import json
import logging
import re

from callbridge.agent.nlu import INTENTS, NLUContext, RuleNLU, normalise
from callbridge.agent.prompts import PromptConfig
from callbridge.config import get_settings

log = logging.getLogger(__name__)

_LEADING = r"^(?:hi|hello|hiya|yeah|yes|ok|okay|right|please|can i|could i|i'd like to|i would like to|i want to|i need to|i wanna)\s+"

SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "instructions_note": {"type": "string"},
        "phrases": {"type": "array", "items": {
            "type": "object",
            "properties": {"intent": {"type": "string", "enum": INTENTS}, "phrase": {"type": "string"}},
            "required": ["intent", "phrase"], "additionalProperties": False}},
    },
    "required": ["rationale", "instructions_note", "phrases"],
    "additionalProperties": False,
}


def _phrase(utterance: str) -> str:
    p = normalise(utterance).lower()
    p = re.sub(r"[^\w' ]", " ", p)
    for _ in range(3):
        p = re.sub(_LEADING, "", p.strip())
    return re.sub(r"\s+", " ", p).strip()


def heuristic(current: PromptConfig, cases: list[dict]) -> tuple[dict[str, list[str]], str, str]:
    """Offline improver: teach the phrases that confused us, labelled by rephrase alignment."""
    nlu = RuleNLU()
    added: dict[str, list[str]] = {}
    from datetime import date

    for case in cases:
        for utterance, intent in case["expect"].get("expected_intents", {}).items():
            ctx = NLUContext(today=date.today(), awaiting="task", practitioner_names=[], config=current)
            if nlu.parse(utterance, ctx).intent == intent:
                continue
            phrase = _phrase(utterance)
            if len(phrase) >= 6 and phrase not in current.intent_phrases.get(intent, []):
                added.setdefault(intent, []).append(phrase)
    rationale = "; ".join(f"teach {intent}: {', '.join(repr(p) for p in ps)}" for intent, ps in added.items())
    return added, "", rationale or "no fix found"


def with_llm(current: PromptConfig, cases: list[dict]) -> tuple[dict[str, list[str]], str, str]:
    import anthropic

    client = anthropic.Anthropic()
    prompt = (
        "You improve the understanding step of an AI phone receptionist for a private physio clinic. "
        "Below are recent calls where it failed (personal data removed), and the current config. Propose the "
        "smallest change that would fix these failures without making other calls worse: phrases that should map "
        "to an intent (general enough to cover rephrasings, specific enough not to misfire), and optionally a short "
        "note to append to the understanding instructions. Never propose anything that relaxes safety, verification "
        "or medical-advice rules.\n\n"
        f"Current intent phrases: {json.dumps(current.intent_phrases)}\n\nFailures:\n"
        + "\n".join(json.dumps({"reasons": c.get("reasons"), "caller_lines": c.get("script"),
                                "labels": c["expect"].get("expected_intents", {})}) for c in cases[:40])
    )
    resp = client.beta.messages.create(
        model="claude-opus-5-5", max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("improver request was refused")
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    added: dict[str, list[str]] = {}
    for item in data["phrases"]:
        phrase = _phrase(item["phrase"])
        if len(phrase) >= 4 and item["intent"] in INTENTS:
            added.setdefault(item["intent"], []).append(phrase)
    return added, data["instructions_note"].strip(), data["rationale"]


def propose(current: PromptConfig, cases: list[dict], new_version: str) -> tuple[PromptConfig | None, str]:
    st = get_settings()
    use_llm = st.llm_provider != "rules" and st.anthropic_api_key
    try:
        added, note, rationale = with_llm(current, cases) if use_llm else heuristic(current, cases)
    except Exception:
        log.exception("LLM improver failed; using the heuristic improver")
        added, note, rationale = heuristic(current, cases)
    if not added and not note:
        return None, rationale
    phrases = {k: list(v) for k, v in current.intent_phrases.items()}
    for intent, ps in added.items():
        phrases[intent] = sorted(set(phrases.get(intent, [])) | set(ps))
    instructions = current.nlu_instructions + (f"\n- {note}" if note else "")
    return current.model_copy(update={"version": new_version, "intent_phrases": phrases, "nlu_instructions": instructions}), rationale
