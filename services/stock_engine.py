"""
Khojati Inventory Management System - Core Stock Engine.

This module contains the ACID-safe stock mutation logic for dispatching
orders, receiving inward goods, and applying manual adjustments.

Every stock mutation follows the pattern:
  1. Validate inputs and check for duplicates.
  2. Begin an immediate transaction.
  3. Read current stock, verify sufficient quantity.
  4. Apply the atomic UPDATE with a WHERE guard (``current_stock >= qty``).
  5. Write immutable ledger entries.
  6. Commit or rollback the entire operation atomically.
"""

import logging
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from models import (
    InwardItem as InwardItemModel,
    InwardReceipt,
    MovementType,
    Order,
    OrderItem as OrderItemModel,
    OrderStatus,
    Product,
)
from schemas import (
    AdjustmentRequest,
    AdjustmentResponse,
    DispatchOrderItemResponse,
    DispatchOrderRequest,
    DispatchOrderResponse,
    InwardReceiptItemResponse,
    InwardReceiptRequest,
    InwardReceiptResponse,
)
from services.idempotency import check_duplicate, generate_idempotency_key
from services.ledger import write_ledger_entry
from services.sheets_queue import sheets_queue

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Dispatch Order
# ═══════════════════════════════════════════════════════════════════════

def dispatch_order(db: Session, order_data: DispatchOrderRequest) -> DispatchOrderResponse:
    """
    Process a dispatch order with full ACID guarantees.

    Steps:
      1. Generate or use the provided idempotency key.
      2. Check for duplicate submissions.
      3. Validate all barcodes exist and stock is sufficient.
      4. Deduct stock using race-condition-safe UPDATE.
      5. Create Order and OrderItem records.
      6. Write immutable ledger entries.
      7. Commit the transaction atomically.

    Args:
        db: Active database session.
        order_data: Validated dispatch order request.

    Returns:
        DispatchOrderResponse with full order details.

    Raises:
        Various exceptions mapped to HTTP error codes by the router.
    """
    # Step 1: Generate idempotency key if not provided.
    idempotency_key = order_data.idempotency_key
    if not idempotency_key:
        idempotency_key = generate_idempotency_key(order_data.order_number)

    # Step 2: Check for duplicate submission.
    existing_order = check_duplicate(db, idempotency_key)
    if existing_order is not None:
        raise DuplicateOrderError(
            f"Order '{order_data.order_number}' has already been submitted "
            f"(existing order ID: {existing_order.id})."
        )

    # Check for duplicate order number.
    existing_by_number = (
        db.query(Order)
        .filter(Order.order_number == order_data.order_number)
        .first()
    )
    if existing_by_number is not None:
        raise DuplicateOrderError(
            f"Order number '{order_data.order_number}' already exists."
        )

    # Step 3: Begin immediate transaction for SQLite write lock.
    db.execute(text("BEGIN IMMEDIATE"))

    try:
        # Step 3a: Validate all products and check stock levels.
        products_map: dict[str, Product] = {}
        insufficient_items: list[dict[str, Any]] = []

        for item in order_data.items:
            product = (
                db.query(Product)
                .filter(Product.barcode == item.barcode)
                .with_for_update()
                .first()
            )
            if product is None:
                db.rollback()
                raise ProductNotFoundError(
                    f"No product found with barcode '{item.barcode}'."
                )

            products_map[item.barcode] = product

            if product.current_stock < item.quantity:
                insufficient_items.append({
                    "barcode": item.barcode,
                    "product_name": product.product_name,
                    "requested": item.quantity,
                    "available": product.current_stock,
                })

        if insufficient_items:
            db.rollback()
            raise InsufficientStockError(
                "Insufficient stock for one or more items.",
                details=insufficient_items,
            )

        # Step 4 & 5: Deduct stock, create order items, write ledger.
        order = Order(
            order_number=order_data.order_number,
            customer_name=order_data.customer_name,
            customer_city=order_data.customer_city,
            packed_by=order_data.packed_by,
            status=OrderStatus.PACKED,
            idempotency_key=idempotency_key,
        )
        db.add(order)
        db.flush()  # Get order.id

        response_items: list[DispatchOrderItemResponse] = []

        for item in order_data.items:
            product = products_map[item.barcode]
            prev_stock = product.current_stock

            # Race-condition-safe stock deduction.
            result = db.execute(
                update(Product)
                .where(Product.id == product.id)
                .where(Product.current_stock >= item.quantity)
                .values(current_stock=Product.current_stock - item.quantity)
            )

            if result.rowcount == 0:
                db.rollback()
                raise InsufficientStockError(
                    f"Race condition: stock for '{product.product_name}' "
                    f"(barcode: {item.barcode}) was modified concurrently. "
                    f"Please retry the order.",
                    details=[{
                        "barcode": item.barcode,
                        "product_name": product.product_name,
                        "requested": item.quantity,
                        "available": "unknown (concurrent modification)",
                    }],
                )

            new_stock = prev_stock - item.quantity

            # Create order item.
            order_item = OrderItemModel(
                order_id=order.id,
                product_id=product.id,
                barcode=item.barcode,
                quantity=item.quantity,
            )
            db.add(order_item)

            # Write immutable ledger entry.
            write_ledger_entry(
                db=db,
                product_id=product.id,
                movement_type=MovementType.DISPATCH,
                reference_number=order_data.order_number,
                prev_balance=prev_stock,
                delta=-item.quantity,
                new_balance=new_stock,
                operator=order_data.packed_by,
                notes=f"Dispatch to {order_data.customer_name}, {order_data.customer_city}",
            )

            response_items.append(DispatchOrderItemResponse(
                barcode=item.barcode,
                product_name=product.product_name,
                quantity=item.quantity,
                previous_stock=prev_stock,
                new_stock=new_stock,
            ))

        # Step 6: Commit the entire transaction.
        db.commit()

        # Refresh to get generated timestamps.
        db.refresh(order)

        # Step 7: Queue Google Sheets sync (non-blocking).
        sheets_queue.push_dispatch({
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "customer_city": order.customer_city,
            "packed_by": order.packed_by,
            "items": [
                {"barcode": i.barcode, "quantity": i.quantity, "product_name": i.product_name}
                for i in response_items
            ],
        })

        logger.info(
            "Dispatch order '%s' committed successfully (%d items).",
            order.order_number,
            len(response_items),
        )

        return DispatchOrderResponse(
            order_id=order.id,
            order_number=order.order_number,
            customer_name=order.customer_name,
            customer_city=order.customer_city,
            packed_by=order.packed_by,
            status=order.status.value,
            idempotency_key=order.idempotency_key,
            items=response_items,
            created_at=order.created_at,
        )

    except (DuplicateOrderError, ProductNotFoundError, InsufficientStockError):
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Unexpected error during dispatch: %s", exc, exc_info=True)
        raise StockEngineError(f"An unexpected error occurred: {exc}") from exc


# ═══════════════════════════════════════════════════════════════════════
# Inward Goods Receipt
# ═══════════════════════════════════════════════════════════════════════

def receive_inward(db: Session, receipt_data: InwardReceiptRequest) -> InwardReceiptResponse:
    """
    Process an inward goods receipt with full ACID guarantees.

    Steps:
      1. Check GRN number uniqueness.
      2. Validate all barcodes exist.
      3. Add stock to each product.
      4. Create InwardReceipt and InwardItem records.
      5. Write immutable ledger entries.
      6. Commit the transaction atomically.

    Args:
        db: Active database session.
        receipt_data: Validated inward receipt request.

    Returns:
        InwardReceiptResponse with full receipt details.
    """
    # Step 1: Check for duplicate GRN.
    existing_grn = (
        db.query(InwardReceipt)
        .filter(InwardReceipt.grn_number == receipt_data.grn_number)
        .first()
    )
    if existing_grn is not None:
        raise DuplicateOrderError(
            f"GRN number '{receipt_data.grn_number}' already exists "
            f"(existing receipt ID: {existing_grn.id})."
        )

    try:
        # Step 2: Validate all barcodes.
        products_map: dict[str, Product] = {}
        for item in receipt_data.items:
            product = (
                db.query(Product)
                .filter(Product.barcode == item.barcode)
                .first()
            )
            if product is None:
                raise ProductNotFoundError(
                    f"No product found with barcode '{item.barcode}'."
                )
            products_map[item.barcode] = product

        # Step 3 & 4: Create receipt header.
        receipt = InwardReceipt(
            grn_number=receipt_data.grn_number,
            challan_number=receipt_data.challan_number,
            supplier_name=receipt_data.supplier_name,
            received_by=receipt_data.received_by,
        )
        db.add(receipt)
        db.flush()  # Get receipt.id

        response_items: list[InwardReceiptItemResponse] = []

        for item in receipt_data.items:
            product = products_map[item.barcode]
            prev_stock = product.current_stock

            # Add stock.
            db.execute(
                update(Product)
                .where(Product.id == product.id)
                .values(current_stock=Product.current_stock + item.quantity)
            )

            new_stock = prev_stock + item.quantity

            # Create inward item.
            inward_item = InwardItemModel(
                receipt_id=receipt.id,
                product_id=product.id,
                barcode=item.barcode,
                quantity=item.quantity,
            )
            db.add(inward_item)

            # Write immutable ledger entry.
            write_ledger_entry(
                db=db,
                product_id=product.id,
                movement_type=MovementType.INWARD_GRN,
                reference_number=receipt_data.grn_number,
                prev_balance=prev_stock,
                delta=item.quantity,
                new_balance=new_stock,
                operator=receipt_data.received_by,
                notes=f"Supplier: {receipt_data.supplier_name}, Challan: {receipt_data.challan_number}",
            )

            response_items.append(InwardReceiptItemResponse(
                barcode=item.barcode,
                product_name=product.product_name,
                quantity=item.quantity,
                previous_stock=prev_stock,
                new_stock=new_stock,
            ))

        # Step 5: Commit atomically.
        db.commit()
        db.refresh(receipt)

        # Step 6: Queue Google Sheets sync.
        sheets_queue.push_inward({
            "grn_number": receipt.grn_number,
            "supplier_name": receipt.supplier_name,
            "items": [
                {"barcode": i.barcode, "quantity": i.quantity, "product_name": i.product_name}
                for i in response_items
            ],
        })

        logger.info(
            "Inward receipt '%s' committed successfully (%d items).",
            receipt.grn_number,
            len(response_items),
        )

        return InwardReceiptResponse(
            receipt_id=receipt.id,
            grn_number=receipt.grn_number,
            challan_number=receipt.challan_number,
            supplier_name=receipt.supplier_name,
            received_by=receipt.received_by,
            items=response_items,
            created_at=receipt.created_at,
        )

    except (DuplicateOrderError, ProductNotFoundError):
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Unexpected error during inward receipt: %s", exc, exc_info=True)
        raise StockEngineError(f"An unexpected error occurred: {exc}") from exc


# ═══════════════════════════════════════════════════════════════════════
# Stock Adjustment
# ═══════════════════════════════════════════════════════════════════════

def adjust_stock(db: Session, adjustment: AdjustmentRequest) -> AdjustmentResponse:
    """
    Apply a manual stock adjustment (damage write-off or return restock).

    Args:
        db: Active database session.
        adjustment: Validated adjustment request.

    Returns:
        AdjustmentResponse with before/after stock levels.
    """
    product = (
        db.query(Product)
        .filter(Product.barcode == adjustment.barcode)
        .first()
    )
    if product is None:
        raise ProductNotFoundError(
            f"No product found with barcode '{adjustment.barcode}'."
        )

    prev_stock = product.current_stock
    new_stock = prev_stock + adjustment.quantity_delta

    # Enforce non-negative stock.
    if new_stock < 0:
        raise InsufficientStockError(
            f"Adjustment would result in negative stock for "
            f"'{product.product_name}' (barcode: {adjustment.barcode}). "
            f"Current stock: {prev_stock}, requested delta: {adjustment.quantity_delta}.",
            details=[{
                "barcode": adjustment.barcode,
                "product_name": product.product_name,
                "current_stock": prev_stock,
                "requested_delta": adjustment.quantity_delta,
                "would_result_in": new_stock,
            }],
        )

    try:
        # Determine movement type.
        movement_type = MovementType(adjustment.movement_type)

        # Apply the stock change with race-condition guard for decrements.
        if adjustment.quantity_delta < 0:
            result = db.execute(
                update(Product)
                .where(Product.id == product.id)
                .where(Product.current_stock >= abs(adjustment.quantity_delta))
                .values(current_stock=Product.current_stock + adjustment.quantity_delta)
            )
            if result.rowcount == 0:
                db.rollback()
                raise InsufficientStockError(
                    f"Concurrent modification detected for '{product.product_name}'. "
                    f"Please retry the adjustment.",
                    details=[],
                )
        else:
            db.execute(
                update(Product)
                .where(Product.id == product.id)
                .values(current_stock=Product.current_stock + adjustment.quantity_delta)
            )

        # Write immutable ledger entry.
        write_ledger_entry(
            db=db,
            product_id=product.id,
            movement_type=movement_type,
            reference_number=f"ADJ-{product.barcode}-{adjustment.movement_type}",
            prev_balance=prev_stock,
            delta=adjustment.quantity_delta,
            new_balance=new_stock,
            operator=adjustment.operator,
            notes=adjustment.notes,
        )

        db.commit()

        # Queue stock update sync.
        sheets_queue.push_stock_update()

        logger.info(
            "Stock adjustment for '%s' (%s): %+d (was %d, now %d).",
            product.product_name,
            adjustment.movement_type,
            adjustment.quantity_delta,
            prev_stock,
            new_stock,
        )

        return AdjustmentResponse(
            barcode=adjustment.barcode,
            product_name=product.product_name,
            movement_type=adjustment.movement_type,
            quantity_delta=adjustment.quantity_delta,
            previous_stock=prev_stock,
            new_stock=new_stock,
            operator=adjustment.operator,
            notes=adjustment.notes,
        )

    except (InsufficientStockError, ProductNotFoundError):
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Unexpected error during adjustment: %s", exc, exc_info=True)
        raise StockEngineError(f"An unexpected error occurred: {exc}") from exc


# ═══════════════════════════════════════════════════════════════════════
# Custom Exceptions
# ═══════════════════════════════════════════════════════════════════════

class StockEngineError(Exception):
    """Base exception for stock engine errors."""
    pass


class DuplicateOrderError(StockEngineError):
    """Raised when a duplicate order or GRN is detected."""
    pass


class ProductNotFoundError(StockEngineError):
    """Raised when a referenced barcode does not exist."""
    pass


class InsufficientStockError(StockEngineError):
    """Raised when stock is insufficient for the requested operation."""

    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.details = details or []
