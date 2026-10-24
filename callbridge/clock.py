"""Injectable clock so evals and tests can pin "now". All times are clinic-local (Europe/London), naive.

The frozen value is process-global (not a ContextVar) on purpose: graph nodes may run on worker
threads, and a pinned clock must hold across them. Only evals and tests ever freeze it."""

from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
_frozen: datetime | None = None


def now() -> datetime:
    if _frozen is not None:
        return _frozen
    return datetime.now(LONDON).replace(tzinfo=None, microsecond=0)


@contextmanager
def frozen_at(moment: datetime):
    global _frozen
    previous, _frozen = _frozen, moment
    try:
        yield
    finally:
        _frozen = previous
