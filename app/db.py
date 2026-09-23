"""SQLAlchemy engine/session bootstrap."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.db_url,
    connect_args={"check_same_thread": False} if _settings.db_url.startswith("sqlite") else {},
)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  (register mappings)

    Base.metadata.create_all(engine)
    # Lightweight column migration for pre-existing sqlite DBs (create_all does
    # not ALTER existing tables).
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql(
                "ALTER TABLE object_refs ADD COLUMN source VARCHAR(20) DEFAULT 'regex'"
            )
            conn.commit()
        except Exception:  # noqa: BLE001  (column already exists)
            pass
