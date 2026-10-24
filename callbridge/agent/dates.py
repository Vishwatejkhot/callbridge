"""Deterministic resolution of spoken dates and times ("next Tuesday", "half three", "the 24th").

Even when an LLM does the understanding, it is asked for the *phrase* and this code resolves it:
date arithmetic is exactly the kind of thing models get subtly wrong. Every booking is also read
back with the full date, so a wrong guess is caught by the caller before anything is changed."""

import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_MON = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "noon": 12, "midday": 12}
ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
                 "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
                 "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
                 "twentieth": 20, "twenty-first": 21, "twenty first": 21, "twenty-second": 22, "twenty second": 22,
                 "twenty-third": 23, "twenty third": 23, "thirtieth": 30, "thirty-first": 31, "thirty first": 31}

_WD = "|".join(WEEKDAYS)
_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year(y: str) -> int:
    n = int(y)
    return n + (2000 if n < 30 else 1900) if n < 100 else n


def parse_full_date(text: str) -> date | None:
    """A date with an explicit year: '12th March 1985', '12/03/1985', '1985-03-12', 'March 12 1985'."""
    t = text.lower()
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", t):  # UK order: day/month/year
        return _safe_date(_year(m[3]), int(m[2]), int(m[1]))
    if m := re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE},?\s+(\d{{2,4}})\b", t):
        return _safe_date(_year(m[3]), _MON[m[2][:4] if m[2][:4] == "sept" else m[2][:3]], int(m[1]))
    if m := re.search(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", t):
        return _safe_date(int(m[3]), _MON[m[1][:3]], int(m[2]))
    for word, n in sorted(ORDINAL_WORDS.items(), key=lambda kv: -len(kv[0])):
        if m := re.search(rf"\b(?:the\s+)?{word}\s+(?:of\s+)?{_MONTH_RE},?\s+(\d{{4}})\b", t):
            return _safe_date(int(m[2]), _MON[m[1][:3]], n)
    return None


_FULL_DATE_PATTERNS = [
    r"\b\d{4}-\d{1,2}-\d{1,2}\b",
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b",
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE},?\s+\d{{2,4}}\b",
    rf"\b{_MONTH_RE}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
    rf"\b(?:the\s+)?[a-z]+(?:[- ][a-z]+)?\s+(?:of\s+)?{_MONTH_RE},?\s+\d{{4}}\b",
]


def strip_full_dates(text: str) -> str:
    """Remove dates-with-a-year (dates of birth) so they aren't mistaken for appointment days."""
    out = text.lower()
    for p in _FULL_DATE_PATTERNS:
        out = re.sub(p, " ", out)
    return re.sub(r"\b(?:date of birth|born on|born|d\.?o\.?b\.?)\b", " ", out)


def parse_day(text: str, today: date, next_weekday_policy: str = "following_week") -> date | None:
    """A day for an appointment (no year, or a future one). Returns None if no day is mentioned."""
    t = text.lower()
    if re.search(r"\b(?:today|this (?:morning|afternoon|evening)|tonight)\b", t):
        return today
    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    if m := re.search(rf"\b(next|this|on|for)?\s*({_WD})\b", t):
        qualifier, wd = m[1], WEEKDAYS.index(m[2])
        ahead = (wd - today.weekday()) % 7 or 7  # nearest future occurrence, never today
        if qualifier == "next" and next_weekday_policy == "following_week":
            # "next Tuesday" = Tuesday of next calendar week (Mon-Sun)
            start_next_week = today + timedelta(days=7 - today.weekday())
            return start_next_week + timedelta(days=wd)
        if qualifier == "next" and next_weekday_policy == "skip_nearest":
            return today + timedelta(days=ahead + 7)
        return today + timedelta(days=ahead)
    if re.search(r"\bnext week\b", t):
        return today + timedelta(days=7 - today.weekday())
    if m := re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\b", t):
        return _roll_forward(today, _MON[m[2][:3]], int(m[1]))
    if m := re.search(rf"\b{_MONTH_RE}\s+(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\b", t):
        return _roll_forward(today, _MON[m[1][:3]], int(m[2]))
    if m := re.search(r"\b(\d{1,2})[/.](\d{1,2})\b(?![/.]\d)", t):
        return _roll_forward(today, int(m[2]), int(m[1]))
    if m := re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", t):
        return _day_of_month(today, int(m[1]))
    for word, n in sorted(ORDINAL_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\bthe {word}\b(?! one| option)", t):
            return _day_of_month(today, n)
    return None


def _roll_forward(today: date, month: int, day: int) -> date | None:
    if not 1 <= month <= 12:
        return None
    d = _safe_date(today.year, month, day)
    if d and d < today:
        d = _safe_date(today.year + 1, month, day)
    return d


def _day_of_month(today: date, n: int) -> date | None:
    d = _safe_date(today.year, today.month, n)
    if d is None or d < today:
        m, y = (today.month % 12) + 1, today.year + (today.month == 12)
        d = _safe_date(y, m, n)
    return d


def parse_time(text: str) -> str | None:
    """'3pm', '3:30 pm', '15:00', 'half past three', 'half three', 'quarter past 10', 'noon' -> 'HH:MM'."""
    t = text.lower().replace(".", ":")
    t = re.sub(r"\b(a\s*m|p\s*m)\b", lambda m: m[1].replace(" ", ""), t)
    if m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t):
        h, mi = int(m[1]), int(m[2] or 0)
        if m[3] == "pm" and h < 12:
            h += 12
        if m[3] == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None
    if m := re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t):
        return _pm_guess(int(m[1]), int(m[2]))
    words = "|".join(NUMBER_WORDS)
    num = rf"(\d{{1,2}}|{words})"
    if m := re.search(rf"\bhalf (?:past )?{num}\b", t):
        return _pm_guess(_n(m[1]), 30, has_suffix=bool(re.search(r"\b(?:in the )?morning\b", t)))
    if m := re.search(rf"\bquarter past {num}\b", t):
        return _pm_guess(_n(m[1]), 15)
    if m := re.search(rf"\bquarter to {num}\b", t):
        return _pm_guess(_n(m[1]) - 1, 45)
    if m := re.search(rf"\b{num} o'?clock\b", t):
        return _pm_guess(_n(m[1]), 0, has_suffix=bool(re.search(r"\bmorning\b", t)))
    if m := re.search(rf"\b(?:at|for|around|about) {num}(?: (\d{{2}}|thirty|fifteen|forty[- ]five))?\b(?! (?:days|weeks|minutes|people))", t):
        mins = {"thirty": 30, "fifteen": 15, "forty-five": 45, "forty five": 45}.get(m[2] or "", None)
        mi = mins if mins is not None else int(m[2]) if m[2] and m[2].isdigit() else 0
        return _pm_guess(_n(m[1]), mi, has_suffix=bool(re.search(r"\bmorning\b", t)))
    if re.search(r"\b(?:noon|midday)\b", t):
        return "12:00"
    if m := re.search(rf"\b({words})\s+(am|pm)\b", t):
        h = _n(m[1]) + (12 if m[2] == "pm" and _n(m[1]) < 12 else 0)
        return f"{h:02d}:00"
    return None


def _n(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def _pm_guess(h: int, mi: int, has_suffix: bool = False) -> str | None:
    """Clinic hours are 8am-7pm: a bare '3' means 3pm, a bare '9' means 9am."""
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    if not has_suffix and 1 <= h <= 7:
        h += 12
    return f"{h:02d}:{mi:02d}"


def parse_daypart(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\b(?:morning|before lunch|early)\b", t):
        return "morning"
    if re.search(r"\b(?:afternoon|after lunch|lunchtime)\b", t):
        return "afternoon"
    if re.search(r"\b(?:evening|after work|late|tonight)\b", t):
        return "evening"
    return None
