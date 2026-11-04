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
from database import init_db, SessionLocal
from models import Product
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
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "damage_photos")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "uploads")), name="uploads")


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
            db.commit()
            logger.info("Seeded %d sample products.", len(sample_products))
        else:
            logger.info("Database already contains %d products. Skipping seed.", count)
    finally:
        db.close()

    logger.info("Khojati Inventory Management System is ready.")


# ── Health Check ──────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    """Basic health check endpoint."""
    return {
        "service": "Khojati Inventory Management System",
        "status": "operational",
        "version": "1.0.0",
    }
