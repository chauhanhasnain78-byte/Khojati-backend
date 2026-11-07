"""
Khojati Inventory Management System - Database Setup.

Configures the database engine (SQLite with WAL mode for local/edge,
or PostgreSQL for cloud hosting), provides session factory, and exposes
an ``init_db`` helper that creates all tables on startup.
"""

import logging
import os
import tempfile

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import settings

logger = logging.getLogger("khojati.database")

db_url = settings.DATABASE_URL

# Normalize PostgreSQL driver prefixes for SQLAlchemy
if db_url.startswith("prisma+postgres://"):
    db_url = db_url.replace("prisma+postgres://", "postgresql://", 1)
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

is_sqlite = db_url.startswith("sqlite")
db_status_info = {"active_url": db_url, "is_sqlite": is_sqlite, "fallback": False}

connect_args = {"check_same_thread": False} if is_sqlite else {"connect_timeout": 5}

try:
    engine = create_engine(
        db_url,
        connect_args=connect_args,
        echo=False,
        pool_pre_ping=True,  # Automatically reconnect dropped cloud connections
    )
except Exception as e:
    logger.error("Failed to initialize database engine with %s: %s. Falling back to SQLite.", db_url, e)
    tmp_db = os.path.join(tempfile.gettempdir(), "khojati_inventory.db")
    db_url = f"sqlite:///{tmp_db}"
    is_sqlite = True
    db_status_info = {"active_url": db_url, "is_sqlite": True, "fallback": True, "error": str(e)}
    connect_args = {"check_same_thread": False}
    engine = create_engine(db_url, connect_args=connect_args, echo=False)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_wal_mode(dbapi_connection, _connection_record):
        """Enable WAL journal mode and enforce foreign keys for SQLite."""
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()
        except Exception:
            pass


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency that yields a database session and ensures cleanup."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables defined in ``models.py`` with automatic fallback on connection failure."""
    global engine, SessionLocal, db_url, is_sqlite, db_status_info
    from models import Base  # noqa: F401 — imported for side-effect

    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.error("Database connection failed during init_db: %s. Falling back to SQLite.", e)
        if not is_sqlite:
            tmp_db = os.path.join(tempfile.gettempdir(), "khojati_inventory.db")
            db_url = f"sqlite:///{tmp_db}"
            is_sqlite = True
            db_status_info = {"active_url": db_url, "is_sqlite": True, "fallback": True, "error": str(e)}
            connect_args = {"check_same_thread": False}
            engine = create_engine(db_url, connect_args=connect_args, echo=False)
            SessionLocal.configure(bind=engine)
            Base.metadata.create_all(bind=engine)
            logger.info("Successfully initialized fallback SQLite database at %s", tmp_db)
        else:
            raise
