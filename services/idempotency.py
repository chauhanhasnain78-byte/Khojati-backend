"""
Khojati Inventory Management System - Idempotency Service.

Generates deterministic idempotency keys and checks for duplicate order
submissions to prevent accidental double-dispatches from the barcode
scanner app.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import Order


def generate_idempotency_key(
    order_number: str,
    timestamp_minute: Optional[str] = None,
    device_id: str = "default",
) -> str:
    """
    Generate a deterministic SHA-256 idempotency key.

    The key is derived from the order number, the current UTC minute
    (truncated to the minute boundary), and an optional device identifier.
    This ensures that the same order submitted from the same device within
    the same minute produces an identical key.

    Args:
        order_number: The unique order number.
        timestamp_minute: ISO-formatted minute string (defaults to now).
        device_id: Identifier for the submitting device.

    Returns:
        A 64-character lowercase hex SHA-256 digest.
    """
    if timestamp_minute is None:
        now = datetime.now(timezone.utc)
        timestamp_minute = now.strftime("%Y-%m-%dT%H:%M")

    raw = f"{order_number}|{timestamp_minute}|{device_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_duplicate(db: Session, key: str) -> Optional[Order]:
    """
    Check whether an order with the given idempotency key already exists.

    Args:
        db: Active database session.
        key: The idempotency key to look up.

    Returns:
        The existing Order if found, or None if no duplicate exists.
    """
    return db.query(Order).filter(Order.idempotency_key == key).first()
