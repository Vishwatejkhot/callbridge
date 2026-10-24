"""Fake patients. A persona has a goal and some quirks; the bot reacts to what the assistant asks,
so scenarios keep working when the dialogue wording or order changes.

ScriptedPatient: deterministic, offline, runs in CI on every pull request.
LLMPatient: Claude plays the persona (more varied phrasing); used for nightly / pre-release runs."""

import json
import random
import re

FILLERS = ["um", "uh", "erm", "so", "like", "sorry"]


class ScriptedPatient:
    def __init__(self, persona: dict, seed: int = 0):
        self.p = persona
        self.rng = random.Random(seed)
        self.dob_attempts = 0
        self.changed_mind = False
        self.rejected_confirm = False
        self.asked_side_question = False
        self.said = 0

    # ------------------------------------------------------------ speech helpers

    def _noisy(self, text: str) -> str:
        level = self.p.get("noise", 0)
        if not level:
            return text
        words = text.split()
        out: list[str] = []
        for i, w in enumerate(words):
            if self.rng.random() < 0.12 * level and i < len(words) - 1:
                out.append(self.rng.choice(FILLERS) + ",")
            out.append(w)
            if self.rng.random() < 0.06 * level and i < len(words) - 1:
                out.append(w)  # stutter / repetition
        text = " ".join(out)
        if level >= 2:
            text = text.lower().rstrip(".?!")
        return text

    def _say(self, text: str) -> str:
        self.said += 1
        return self._noisy(text)

    def _dob_text(self, wrong: bool = False) -> str:
        d, m, y = (int(x) for x in self.p["dob"].split("-")[::-1])
        if wrong:
            d = d % 27 + 1 if d != 27 else 3
        fmt = self.p.get("dob_format", 0)
        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"]
        suffix = "th" if 11 <= d <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return [f"{d}{suffix} of {months[m - 1]} {y}", f"{d:02d}/{m:02d}/{y}", f"{months[m - 1]} {d} {y}"][fmt % 3]

    def _identity(self, bot: str) -> str:
        wrong = (self.p.get("wrong_dob_first") and self.dob_attempts == 0) or self.p.get("wrong_person")
        self.dob_attempts += 1
        dob = self._dob_text(wrong=bool(wrong))
        name = f"{self.p['first_name']} {self.p['last_name']}"
        if "date of birth" in bot and ("full name" in bot or "surname" in bot):
            return self._say(f"Yes, it's {name}, {dob}" if self.p.get("dob_format", 0) != 1 else f"{name}, date of birth {dob}")
        if "full name" in bot or "surname" in bot:
            return self._say(f"It's {name}")
        return self._say(f"It's {dob}" if self.rng.random() < 0.5 else dob)

    # ------------------------------------------------------------ dialogue

    def opening(self) -> str:
        return self._say(self.p["opening"])

    def respond(self, bot: str) -> str | None:
        b = bot.lower()
        goal = self.p["goal"]
        if "goodbye" in b or "put you through" in b:
            return None
        if "date of birth" in b or "full name" in b or "surname" in b or "couldn't match" in b:
            return self._identity(b)
        if "is it the one" in b:
            return self._say(f"The one on {self.p.get('target_day_phrase', 'the first one')}")
        if "what message" in b or "pass on" in b:
            return self._say(self.p.get("message", "Could someone call me back about my appointment please?"))
        if "take a message" in b and b.rstrip().endswith("?"):
            return self._say("Yes please" if goal in ("message", "question_unknown") else "No thanks")
        if "which works better" in b or "would that work" in b or "which would you prefer" in b:
            if self.p.get("change_mind_to") and not self.changed_mind:
                self.changed_mind = True
                return self._say(f"Actually, sorry, could I do {self.p['change_mind_to']} instead?")
            return self._say(self.p.get("pick", "The first one please"))
        if "suit you" in b:
            if self.p.get("change_mind_to") and self.changed_mind and self.p.get("change_mind_to_when"):
                return self._say(self.p["change_mind_to_when"])
            return self._say(self.p.get("when", "Any time is fine"))
        if "shall i" in b or "is that right" in b or "go ahead" in b:
            if self.p.get("reject_first_confirm") and not self.rejected_confirm:
                self.rejected_confirm = True
                return self._say(f"No, sorry, can we do {self.p['reject_first_confirm']} instead")
            return self._say(self.rng.choice(["Yes please", "Yes, that's right", "Perfect, yes", "Yeah go ahead"]))
        if "anything else" in b or "what can i help" in b or "how can i help" in b:
            if self.p.get("side_question") and not self.asked_side_question:
                self.asked_side_question = True
                return self._say(self.p["side_question"])
            if self.p.get("follow_up") and self.said < 3:
                return self._say(self.p["follow_up"])
            return self._say(self.rng.choice(["No, that's all, thanks", "No thanks, bye", "That's everything, thank you"]))
        if "would you like to book" in b:
            return self._say("No thanks, bye")
        if "didn't catch" in b or "didn't quite catch" in b or "trouble understanding" in b:
            return self._say(self.p.get("rephrase", self.p["opening"]))
        if self.said > 14:
            return None
        return self._say(self.p.get("fallback", "Sorry, what was that?"))


class ReplayPatient:
    """Replays a real (redacted) failed call line by line: regression tests from production."""

    def __init__(self, script: list[str]):
        self.lines = list(script)

    def opening(self) -> str:
        return self.lines.pop(0) if self.lines else "Hello?"

    def respond(self, bot: str) -> str | None:
        return self.lines.pop(0) if self.lines else None


class LLMPatient:
    """Claude role-plays the caller. Same interface as ScriptedPatient."""

    def __init__(self, persona: dict, seed: int = 0):
        import anthropic

        self.p = persona
        self.client = anthropic.Anthropic()
        self.history: list[dict] = []
        self.system = (
            "You are role-playing a patient phoning a private physio clinic, to test its AI receptionist. "
            "Speak like a real caller on the phone: short, natural, sometimes informal. Say ONLY the caller's next line. "
            "If the conversation is finished, reply exactly <hangup>. Your persona and goal:\n"
            + json.dumps({k: v for k, v in persona.items() if k not in ("opening",)}, indent=1)
        )

    def opening(self) -> str:
        self.history.append({"role": "user", "content": "(the receptionist has just greeted you) Start the call."})
        self.history.append({"role": "assistant", "content": self.p["opening"]})
        return self.p["opening"]

    def respond(self, bot: str) -> str | None:
        self.history.append({"role": "user", "content": f"Receptionist: {bot}"})
        resp = self.client.messages.create(model="claude-opus-5-5", max_tokens=1024, output_config={"effort": "low"},
                                           system=self.system, messages=self.history)
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        self.history.append({"role": "assistant", "content": text or "<hangup>"})
        if not text or "<hangup>" in text or len(self.history) > 40:
            return None
        return re.sub(r"^(?:caller|patient)\s*:\s*", "", text, flags=re.I)
