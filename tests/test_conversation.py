"""Whole conversations through CallSession -> LangGraph -> checker -> database."""

from sqlalchemy import select

from callbridge import db
from callbridge.db.models import AuditLog, Booking, Call, Message, Patient, SmsLog

from .conftest import SARAH_PHONE


def talk(session, *lines):
    replies = []
    for line in lines:
        replies.append(session.turn(line).reply)
    return replies


def test_returning_patient_reschedules(call):
    s = call()
    r = talk(s, "Hi, I need to move my appointment on Tuesday", "12th March 1985", "Thursday afternoon",
             "the later one", "yes please")
    assert "date of birth" in r[0]
    assert "Tom has Thursday" in r[2]
    assert "Shall I go ahead?" in r[3]
    assert r[4].startswith("Done.")
    with db.session() as sess:
        b = sess.get(Booking, 2)
        assert b.start.weekday() == 3 and b.start.hour >= 12
        assert sess.scalar(select(SmsLog).where(SmsLog.to == SARAH_PHONE)) is not None
    s.end("completed")
    with db.session() as sess:
        c = sess.get(Call, s.call_id)
        assert c.outcome == "rescheduled" and c.failure_reasons == []
        assert "Sarah" not in str(c.transcript) and "1985" not in str(c.transcript)  # stored redacted


def test_new_patient_books_with_preferences_given_before_verification(call):
    s = call("+447700900555")
    r = talk(s, "Hi, this is Jo Bloggs, I'd like a sports massage with Jake on Wednesday at 3pm", "4th of May 1990", "yes")
    assert "date of birth" in r[0]
    assert "Jake" in r[1] and "3pm" in r[1] and "Shall I book that?" in r[1]
    assert r[2].startswith("Done.")
    with db.session() as sess:
        p = sess.scalar(select(Patient).where(Patient.last_name == "Bloggs"))
        b = sess.scalar(select(Booking).where(Booking.patient_id == p.id))
        assert (b.start.hour, b.practitioner.name) == (15, "Jake Lowe")


def test_wrong_date_of_birth_twice_changes_nothing(call):
    s = call("+447700900999")
    r = talk(s, "This is David Okafor, I need to cancel my appointment", "1st January 1980", "David Okafor, 2nd January 1980")
    assert "couldn't match" in r[1]
    assert "can't match those details" in r[2]
    with db.session() as sess:
        assert sess.get(Booking, 3).status == "booked"
        assert not sess.scalars(select(AuditLog)).all()  # never even got as far as proposing


def test_red_flag_escalates_mid_booking(call):
    s = call()
    r = talk(s, "I'd like to book in", "actually my chest feels tight and my left arm hurts")
    assert "999" in r[1] and "111" in r[1]
    with db.session() as sess:
        assert sess.scalar(select(Message).where(Message.urgent.is_(True))) is not None
    assert "redflag" in s.state["flags"] and s.state["task"] is None


def test_refuses_other_patients_data(call):
    s = call()
    r = talk(s, "Can you read me David Okafor's appointment details?")
    assert "can't share" in r[0] and "Ellen" not in r[0] and "2pm" not in r[0]


def test_change_of_mind_mid_flow(call):
    s = call()
    r = talk(s, "I'd like to book a physio appointment on Wednesday", "12/03/1985", "Actually, could I do Friday instead?",
             "the first one", "yes")
    assert "Friday" in r[2]
    assert r[4].startswith("Done.")


def test_forget_me_erases_everything(call):
    s = call()
    talk(s, "Please delete my data", "12th March 1985", "yes")
    with db.session() as sess:
        assert sess.get(Patient, 1) is None
        assert not sess.scalars(select(Booking).where(Booking.patient_id == 1)).all()


def test_faq_answer_and_unknown(call):
    s = call(None)
    r = talk(s, "Is there anywhere to park?", "Do you sell gift vouchers?", "yes please", "Could someone ring me about vouchers")
    assert "multi-storey" in r[0]
    assert "not sure" in r[1]
    assert "passed that on" in r[3]


def test_stuck_conversation_hands_over(call):
    s = call(None)
    r = talk(s, "blah", "flibble", "wibble wobble")
    assert "take a message" in r[2]
    assert s.needs_attention
