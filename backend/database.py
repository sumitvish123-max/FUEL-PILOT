"""Database engine, session factory and shared time helper.

SQLite is used by default so the project runs with zero setup. To move to
PostgreSQL set  FUELPILOT_DB_URL=postgresql+psycopg2://user:pass@host/fuelpilot
(and `pip install psycopg2-binary`). No other code changes are needed.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_URL = os.getenv("FUELPILOT_DB_URL", f"sqlite:///{DATA_DIR / 'fuelpilot.db'}")

# India has no DST, so a fixed +05:30 offset is exact and avoids needing tzdata on Windows.
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Current station-local time as a naive datetime (all timestamps are stored in IST)."""
    return datetime.now(IST).replace(tzinfo=None)


_is_sqlite = DB_URL.startswith("sqlite")
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # readers don't block the writer
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables on Base.metadata)
    Base.metadata.create_all(bind=engine)
