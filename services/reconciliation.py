"""
Khojati Inventory Management System - Reconciliation Service.

Generates and retrieves end-of-day (EOD) snapshots for daily stock
reconciliation and reporting.
"""

import json
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import EODSnapshot, Product


def generate_eod_snapshot(db: Session, snapshot_date: Optional[date] = None) -> dict:
    """
    Generate an end-of-day stock snapshot for the given date.

    Captures the current stock level, low-stock threshold, and category
    for every product in the catalogue and stores it as a JSON blob.

    Args:
        db: Active database session.
        snapshot_date: The date for this snapshot (defaults to today UTC).

    Returns:
        Dictionary with snapshot metadata and per-product data.

    Raises:
        ValueError: If a snapshot already exists for the given date.
    """
    if snapshot_date is None:
        snapshot_date = datetime.now(timezone.utc).date()

    date_str = snapshot_date.isoformat()

    # Check for existing snapshot.
    existing = (
        db.query(EODSnapshot)
        .filter(EODSnapshot.snapshot_date == date_str)
        .first()
    )
    if existing is not None:
        raise ValueError(
            f"An end-of-day snapshot already exists for {date_str}. "
            f"Each date may only have one snapshot."
        )

    # Capture current stock levels.
    products = db.query(Product).order_by(Product.barcode).all()
    product_snapshots = []

    for product in products:
        product_snapshots.append({
            "product_id": product.id,
            "barcode": product.barcode,
            "product_name": product.product_name,
            "category": product.category,
            "current_stock": product.current_stock,
            "low_stock_threshold": product.low_stock_threshold,
            "is_low_stock": product.current_stock < product.low_stock_threshold,
        })

    snapshot = EODSnapshot(
        snapshot_date=date_str,
        product_snapshots=json.dumps(product_snapshots, ensure_ascii=False),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return {
        "snapshot_id": snapshot.id,
        "snapshot_date": date_str,
        "total_products": len(product_snapshots),
        "products": product_snapshots,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


def get_snapshot(db: Session, snapshot_date: date) -> Optional[dict]:
    """
    Retrieve a previously generated EOD snapshot for a specific date.

    Args:
        db: Active database session.
        snapshot_date: The date to look up.

    Returns:
        Dictionary with snapshot data, or None if no snapshot exists.
    """
    date_str = snapshot_date.isoformat()
    snapshot = (
        db.query(EODSnapshot)
        .filter(EODSnapshot.snapshot_date == date_str)
        .first()
    )

    if snapshot is None:
        return None

    return {
        "snapshot_id": snapshot.id,
        "snapshot_date": snapshot.snapshot_date,
        "total_products": len(json.loads(snapshot.product_snapshots)),
        "products": json.loads(snapshot.product_snapshots),
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }
