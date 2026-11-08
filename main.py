"""
Khojati Inventory Management System - FastAPI Application Entry Point.

Initialises the database, seeds sample products, registers all routers,
and configures CORS middleware.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from auth import create_access_token, hash_pin, verify_pin
from config import settings
from database import init_db, SessionLocal, db_status_info
from models import MovementType, Product, StockLedger
from schemas import PinVerifyRequest, TokenResponse

# ── Logging Configuration ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("khojati")


# ── Application Factory ───────────────────────────────────────────────

app = FastAPI(
    title="Khojati Inventory Management System",
    description=(
        "Audit-proof inventory management API with barcode scanning, "
        "ACID transactions, immutable ledger, and live Google Sheets sync."
    ),
    version="1.0.0",
)

# ── CORS Middleware ────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static Files (Damage Photos) ──────────────────────────────────────
import os
import tempfile

if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "khojati_uploads", "damage_photos")
else:
    UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "damage_photos")

try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=os.path.dirname(UPLOAD_DIR)), name="uploads")
except Exception as e:
    logger.warning("Could not mount static uploads directory: %s", e)


# ── Root Health Check ─────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root_health_check():
    """Health check endpoint to verify backend status."""
    return {
        "status": "online",
        "app": "Khojati Inventory Management API",
        "version": "1.0.0",
        "docs": "/docs",
        "database": {
            "type": "sqlite" if db_status_info.get("is_sqlite") else "postgresql",
            "fallback": db_status_info.get("fallback", False),
        },
        "message": "Khojati backend serverless engine is operational.",
    }


# ── Register Routers ──────────────────────────────────────────────────
from routers import products, orders, inward, adjustments, dashboard, sheets_sync, app_update

app.include_router(products.router)
app.include_router(orders.router)
app.include_router(inward.router)
app.include_router(adjustments.router)
app.include_router(dashboard.router)
app.include_router(sheets_sync.router)
app.include_router(app_update.router)


# ── Auth Endpoint ─────────────────────────────────────────────────────

@app.post("/api/auth/verify-pin", response_model=TokenResponse, tags=["Authentication"])
def verify_admin_pin(data: PinVerifyRequest):
    """
    Verify admin PIN and return a JWT access token.

    The token is valid for 60 minutes. All admin endpoints require
    this token in the Authorization header.
    """
    if not verify_pin(data.pin):
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect PIN. Access denied.",
        )

    token = create_access_token({"sub": "admin", "role": "admin"})
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )


# ── Startup: Database Init & Seed Data ────────────────────────────────

@app.on_event("startup")
def on_startup():
    """Initialise database tables and seed sample Khojati products."""
    logger.info("Initialising database...")
    try:
        init_db()

        db = SessionLocal()
        try:
            from seed_data import seed_master_products
            inserted, updated = seed_master_products(db)
            total = db.query(Product).count()
            logger.info("Master catalog sync: %d inserted, %d updated. Total products in catalog: %d", inserted, updated, total)
        finally:
            db.close()
    except Exception as e:
        logger.error("Database initialization warning: %s", e)

    logger.info("Khojati Inventory Management System is ready.")
