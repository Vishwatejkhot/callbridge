"""Engine/session management with per-clinic scoping.

On Postgres every transaction runs `SET LOCAL app.clinic_id = ...` so row-level security policies
restrict the connection to one clinic. Application code still filters by clinic_id as well
(defence in depth, and so SQLite dev/test databases behave the same)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from callbridge.config import get_settings
from callbridge.db.models import Base

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None

RLS_SQL = Path(__file__).resolve().parents[2] / "infra" / "sql" / "001_rls.sql"


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


def configure(url: str | None = None) -> Engine:
    global _engine, _factory
    _engine = make_engine(url or get_settings().database_url)
    _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


@contextmanager
def isolated(url: str = "sqlite://") -> Iterator[Engine]:
    """Temporarily point the app at another database (evals use a fresh in-memory DB per scenario)."""
    global _engine, _factory
    saved = (_engine, _factory)
    eng = configure(url)
    try:
        yield eng
    finally:
        eng.dispose()
        _engine, _factory = saved


def engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def init_db() -> None:
    eng = engine()
    Base.metadata.create_all(eng)
    if eng.dialect.name == "postgresql" and RLS_SQL.exists():
        with eng.begin() as conn:
            conn.execute(text(RLS_SQL.read_text()))


def scope(s: Session, clinic_id: int) -> None:
    """Switch an open transaction to a clinic (e.g. right after creating that clinic)."""
    if s.bind.dialect.name == "postgresql":
        s.execute(text("SELECT set_config('app.clinic_id', :cid, true)"), {"cid": str(clinic_id)})


@contextmanager
def session(clinic_id: int | None = None) -> Iterator[Session]:
    """A transaction. Pass clinic_id for anything touching clinic data (enables RLS on Postgres)."""
    if _factory is None:
        configure()
    assert _factory is not None
    s = _factory()
    try:
        if clinic_id is not None:
            scope(s, clinic_id)
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
