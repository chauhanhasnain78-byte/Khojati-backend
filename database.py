"""
Khojati Inventory Management System - Database Setup.

Configures the database engine (SQLite with WAL mode for local/edge,
or PostgreSQL for cloud hosting), provides session factory, and exposes
an ``init_db`` helper that creates all tables on startup.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import settings

db_url = settings.DATABASE_URL

# Normalize PostgreSQL driver prefixes for SQLAlchemy
if db_url.startswith("prisma+postgres://"):
    db_url = db_url.replace("prisma+postgres://", "postgresql://", 1)
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

is_sqlite = db_url.startswith("sqlite")

connect_args = {"check_same_thread": False} if is_sqlite else {}

engine = create_engine(
    db_url,
    connect_args=connect_args,
    echo=False,
    pool_pre_ping=True,  # Automatically reconnect dropped cloud connections
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_wal_mode(dbapi_connection, _connection_record):
        """Enable WAL journal mode and enforce foreign keys for SQLite."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency that yields a database session and ensures cleanup."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables defined in ``models.py``."""
    from models import Base  # noqa: F401 — imported for side-effect

    Base.metadata.create_all(bind=engine)
