from datetime import date

import pytest

from callbridge.agent import dates

MON = date(2026, 10, 19)  # a Monday
THU = date(2026, 10, 22)


@pytest.mark.parametrize("text,today,expected", [
    ("tomorrow", MON, date(2026, 10, 20)),
    ("on Thursday", MON, date(2026, 10, 22)),
    ("Monday", MON, date(2026, 10, 26)),  # never today
    ("next Tuesday", MON, date(2026, 10, 27)),  # Tuesday of next week, not tomorrow
    ("next Friday", THU, date(2026, 10, 30)),
    ("this Friday", THU, date(2026, 10, 23)),
    ("the 24th of October", MON, date(2026, 10, 24)),
    ("on the 5th", MON, date(2026, 11, 5)),  # the 5th has passed this month
    ("on the 30th", MON, date(2026, 10, 30)),
    ("October 3rd", MON, date(2027, 10, 3)),  # rolls into next year
    ("next week", THU, date(2026, 10, 26)),
    ("I don't mind", MON, None),
])
def test_parse_day(text, today, expected):
    assert dates.parse_day(text, today) == expected


def test_next_weekday_policies_differ():
    assert dates.parse_day("next Tuesday", MON, "nearest") == date(2026, 10, 20)
    assert dates.parse_day("next Tuesday", MON, "following_week") == date(2026, 10, 27)


@pytest.mark.parametrize("text,expected", [
    ("at 3pm", "15:00"), ("3:30 pm", "15:30"), ("15:00", "15:00"), ("half past two", "14:30"),
    ("quarter past 10", "10:15"), ("at 9", "09:00"), ("at 4", "16:00"), ("noon", "12:00"),
    ("four o'clock", "16:00"), ("12am", "00:00"), ("three pm", "15:00"), ("nothing here", None),
])
def test_parse_time(text, expected):
    assert dates.parse_time(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("12th March 1985", date(1985, 3, 12)), ("12/03/1985", date(1985, 3, 12)), ("March 12 1985", date(1985, 3, 12)),
    ("1985-03-12", date(1985, 3, 12)), ("the twelfth of March 1985", date(1985, 3, 12)), ("31/02/1990", None),
])
def test_parse_full_date(text, expected):
    assert dates.parse_full_date(text) == expected


def test_dob_is_not_mistaken_for_appointment_day():
    assert dates.parse_day(dates.strip_full_dates("it's Sarah Jones, 12th of March 1985"), MON) is None
