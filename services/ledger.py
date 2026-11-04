"""
Khojati Inventory Management System - Stock Ledger Service.

Manages the immutable audit ledger and provides paginated queries
plus mathematical integrity verification.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import MovementType, Product, StockLedger


def write_ledger_entry(
    db: Session,
    product_id: int,
    movement_type: MovementType,
    reference_number: str,
    prev_balance: int,
    delta: int,
    new_balance: int,
    operator: str,
    notes: str = "",
) -> StockLedger:
    """
    Append an immutable entry to the stock ledger.

    This function does NOT commit the transaction; the caller is
    responsible for committing as part of a larger atomic operation.

    Args:
        db: Active database session.
        product_id: ID of the affected product.
        movement_type: Category of stock movement.
        reference_number: Order number, GRN number, or adjustment reference.
        prev_balance: Stock level before this movement.
        delta: Signed quantity change (negative for deductions).
        new_balance: Stock level after this movement.
        operator: Name of the person who initiated the movement.
        notes: Optional descriptive notes.

    Returns:
        The newly created StockLedger instance (not yet committed).
    """
    entry = StockLedger(
        product_id=product_id,
        movement_type=movement_type,
        reference_number=reference_number,
        previous_balance=prev_balance,
        quantity_delta=delta,
        new_balance=new_balance,
        operator=operator,
        notes=notes,
    )
    db.add(entry)
    return entry


def get_ledger_entries(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """
    Retrieve paginated ledger entries with optional date filtering.

    Args:
        db: Active database session.
        page: Page number (1-indexed).
        page_size: Number of entries per page.
        date_from: Include entries on or after this timestamp.
        date_to: Include entries on or before this timestamp.

    Returns:
        Dictionary with ``entries``, ``total_count``, ``page``,
        ``page_size``, and ``total_pages``.
    """
    query = (
        db.query(StockLedger)
        .join(Product, StockLedger.product_id == Product.id)
    )

    if date_from is not None:
        query = query.filter(StockLedger.created_at >= date_from)
    if date_to is not None:
        query = query.filter(StockLedger.created_at <= date_to)

    total_count = query.count()
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    entries = (
        query
        .order_by(StockLedger.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "entries": entries,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def verify_integrity(db: Session) -> dict:
    """
    Verify the mathematical invariant of the stock ledger.

    For every product, the sum of all ``quantity_delta`` values in the
    ledger must equal the product's ``current_stock``.

    Returns:
        Dictionary with ``is_valid`` (bool), ``total_products`` checked,
        and a list of ``discrepancies`` (if any).
    """
    products = db.query(Product).all()
    discrepancies = []

    for product in products:
        ledger_sum = (
            db.query(func.coalesce(func.sum(StockLedger.quantity_delta), 0))
            .filter(StockLedger.product_id == product.id)
            .scalar()
        )

        if ledger_sum != product.current_stock:
            discrepancies.append({
                "product_id": product.id,
                "barcode": product.barcode,
                "product_name": product.product_name,
                "current_stock": product.current_stock,
                "ledger_sum": int(ledger_sum),
                "drift": product.current_stock - int(ledger_sum),
            })

    return {
        "is_valid": len(discrepancies) == 0,
        "total_products": len(products),
        "discrepancies": discrepancies,
    }
