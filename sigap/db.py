"""SQLite engine (WAL mode, foreign keys on) and schema migrations via Alembic."""
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, create_engine

from .config import ROOT

DB_URL = os.environ.get("SIGAP_DATABASE_URL", f"sqlite:///{ROOT / 'var' / 'sigap.db'}")

if DB_URL.startswith("sqlite:///"):
    Path(DB_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    if DB_URL.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # the bot and the admin panel read/write concurrently
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


@contextmanager
def session_scope():
    with Session(engine, expire_on_commit=False) as s:
        yield s


def get_session():
    """FastAPI dependency."""
    with Session(engine, expire_on_commit=False) as s:
        yield s


def migrate() -> None:
    """Brings the schema to the latest Alembic revision."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    command.upgrade(cfg, "head")
