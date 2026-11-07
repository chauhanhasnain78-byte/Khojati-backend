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
from routers import products, orders, inward, adjustments, dashboard, sheets_sync

app.include_router(products.router)
app.include_router(orders.router)
app.include_router(inward.router)
app.include_router(adjustments.router)
app.include_router(dashboard.router)
app.include_router(sheets_sync.router)


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
            count = db.query(Product).count()
            if count == 0:
                logger.info("Seeding sample Khojati products...")
                sample_products = [
                    Product(
                        barcode="8901234567890",
                        product_name="Khojati Surma Sada 50g",
                        category="Eye Care",
                        unit="pcs",
                        current_stock=100,
                        low_stock_threshold=20,
                    ),
                    Product(
                        barcode="8901234567891",
                        product_name="Khojati Surma Khas 25g",
                        category="Eye Care",
                        unit="pcs",
                        current_stock=80,
                        low_stock_threshold=15,
                    ),
                    Product(
                        barcode="8901234567892",
                        product_name="Khojati Mamira Eye Drops 10ml",
                        category="Eye Care",
                        unit="bottles",
                        current_stock=150,
                        low_stock_threshold=25,
                    ),
                    Product(
                        barcode="8901234567893",
                        product_name="Khojati Dant Manjan 100g",
                        category="Oral Care",
                        unit="pcs",
                        current_stock=200,
                        low_stock_threshold=30,
                    ),
                    Product(
                        barcode="8901234567894",
                        product_name="Khojati Rose Water 100ml",
                        category="Personal Care",
                        unit="bottles",
                        current_stock=120,
                        low_stock_threshold=20,
                    ),
                ]
                db.add_all(sample_products)
                db.flush()

                # Seed initial immutable ledger records for mathematical integrity
                for p in sample_products:
                    ledger_entry = StockLedger(
                        product_id=p.id,
                        movement_type=MovementType.INWARD_GRN,
                        reference_number="INITIAL-STOCK-SEED",
                        previous_balance=0,
                        quantity_delta=p.current_stock,
                        new_balance=p.current_stock,
                        operator="System Init",
                        notes="Opening inventory balance initialization",
                    )
                    db.add(ledger_entry)

                db.commit()
                logger.info("Seeded %d sample products and initial audit ledger entries.", len(sample_products))
            else:
                logger.info("Database already contains %d products. Skipping seed.", count)
        finally:
            db.close()
    except Exception as e:
        logger.error("Database initialization warning: %s", e)

    logger.info("Khojati Inventory Management System is ready.")
