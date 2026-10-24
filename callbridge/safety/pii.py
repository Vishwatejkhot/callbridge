"""PII redaction for logs, traces, transcripts stored for review, and auto-generated test cases."""

import logging
import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"(?:\+44\s?|0)(?:\d\s?){9,10}\b"), "[PHONE]"),
    (re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.I), "[NI_NUMBER]"),
    (re.compile(r"\b\d{3}\s?\d{3}\s?\d{4}\b"), "[NHS_NUMBER]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
    (re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I), "[POSTCODE]"),
    (re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"), "[DATE]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "[DATE]"),
    (re.compile(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(?:19|20)\d{2}\b",
        re.I), "[DATE]"),
    (re.compile(r"\b(?i:my name is|this is|i'm|i am|name's|it's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z'-]+)?)"), None),
]


def redact(text: str, known_names: list[str] | None = None) -> str:
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        if repl is None:
            out = pattern.sub(lambda m: m.group(0).replace(m.group(1), "[NAME]"), out)
        else:
            out = pattern.sub(repl, out)
    for name in known_names or []:
        for part in name.split():
            if len(part) > 1:
                out = re.sub(rf"\b{re.escape(part)}\b", "[NAME]", out, flags=re.I)
    return out


class RedactingFilter(logging.Filter):
    """Attach to every log handler so nothing personal reaches log storage."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True
