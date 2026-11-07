"""
Khojati Inventory Management System - Application Configuration.

Central configuration for database, authentication, Google Sheets
integration, and stock management thresholds. Supports environment variables
for seamless cloud deployment (Vercel, Render, Railway, etc.).
"""

import os
import tempfile


def _get_default_db_url() -> str:
    """Return SQLite URL, safely using /tmp in serverless (Vercel/Lambda)."""
    if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        tmp_db = os.path.join(tempfile.gettempdir(), "khojati_inventory.db")
        return f"sqlite:///{tmp_db}"
    return "sqlite:///./khojati_inventory.db"


def _is_usable_db_url(url: str | None) -> bool:
    """Check if connection string is a direct TCP PostgreSQL URL (not an HTTP/WebSocket proxy)."""
    if not url:
        return False
    # Prisma Accelerate is an HTTP/WebSocket proxy for JS Prisma Client, not a raw PostgreSQL server.
    if "accelerate.prisma-data.net" in url:
        return False
    return True


def _find_database_url() -> str:
    """Detect database connection string from various cloud provider env vars."""
    # Prioritise direct PostgreSQL connection strings over poolers / proxies
    keys = [
        "POSTGRES_URL_NON_POOLING",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "DATABASE_URL",
        "PRISMA_DATABASE_URL",
        "STORAGE_URL",
    ]
    for key in keys:
        val = os.getenv(key)
        if _is_usable_db_url(val):
            return val

    # Scan for any env var containing a usable postgres connection string
    for _, val in os.environ.items():
        if val and _is_usable_db_url(val) and ("postgres://" in val or "postgresql://" in val):
            return val

    return _get_default_db_url()


class _Settings:
    """Application-wide configuration constants."""

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str = _find_database_url()

    # ── Authentication ─────────────────────────────────────────────────────
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY",
        "a7f3c9e1d4b8f2a5c6e0d3b7a9f1c4e8d2b6a0f3c7e9d1b5a8f2c4e6d0b3a7",
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # Default admin PIN: "8899" — pre-computed bcrypt hash.
    ADMIN_PIN_HASH: str = os.getenv(
        "ADMIN_PIN_HASH",
        "$2b$12$77st0OYXq7SMCl5Wv1Aj0.4PG5al8oOCWBjd3jCm9y0R2TD3sD.sG",
    )

    # ── Google Sheets Integration ──────────────────────────────────────────
    GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")
    GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")

    # ── Stock Thresholds ───────────────────────────────────────────────────
    DEFAULT_LOW_STOCK_THRESHOLD: int = 20


settings = _Settings()
