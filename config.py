"""
Khojati Inventory Management System - Application Configuration.

Central configuration for database, authentication, Google Sheets
integration, and stock management thresholds. Supports environment variables
for seamless cloud deployment (Render, Railway, etc.).
"""

import os


class _Settings:
    """Application-wide configuration constants."""

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./khojati_inventory.db")

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
