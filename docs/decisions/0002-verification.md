# 0002: Caller verification and greeting

**Status:** accepted

* Changing anything requires date of birth **plus** either the registered phone number (caller ID) or
  the full name. A phone number alone is never enough: numbers are shared (family landlines) and spoofable.
* Two failed attempts mean no changes over the phone. The AI offers to take a message for staff.
* New patients can *book* after giving their name and date of birth (that creates a record). They cannot
  reschedule or cancel anything except what they booked.
* **The greeting does not use the caller's name**, even when caller ID matches a patient. The plan's
  "Hi Sarah, same physio as last time?" would tell whoever picked up Sarah's phone that she is a patient.
  Personalisation ("would you like to see Tom again, like last time?") happens only after verification.
