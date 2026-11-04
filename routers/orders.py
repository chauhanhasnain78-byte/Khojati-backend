"""
Khojati Inventory Management System - Order Management Router.

Endpoints for creating orders (by Sir/Admin), listing pending orders
for packers, and verifying/dispatching orders via barcode scanning.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from auth import get_current_admin
from database import get_db
from models import (
    DispatchPurpose,
    MovementType,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)
from services.idempotency import generate_idempotency_key
from services.ledger import write_ledger_entry
from services.sheets_queue import sheets_queue

router = APIRouter(prefix="/api", tags=["Orders"])


# ═══════════════════════════════════════════════════════════════════════
# Sir Creates an Order (Admin Only)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/admin/orders")
def create_order(
    data: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """
    Create a new order with items and reserve stock.

    Sir specifies: order_number, customer_name, customer_city,
    purpose (SALE/SAMPLE), and items [{barcode, quantity}].

    Stock is reserved immediately so other Sirs see accurate
    available-to-sell numbers.
    """
    order_number = data.get("order_number", "").strip()
    customer_name = data.get("customer_name", "").strip()
    customer_city = data.get("customer_city", "").strip()
    purpose = data.get("purpose", "SALE").upper()
    items = data.get("items", [])
    created_by = admin.get("sub", "Admin")

    if not order_number:
        raise HTTPException(status_code=400, detail="Order number is required.")
    if not customer_name:
        raise HTTPException(status_code=400, detail="Customer name is required.")
    if not items:
        raise HTTPException(status_code=400, detail="At least one item is required.")

    # Check duplicate order number.
    existing = db.query(Order).filter(Order.order_number == order_number).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Order '{order_number}' already exists.")

    # Validate purpose.
    try:
        dispatch_purpose = DispatchPurpose(purpose)
    except ValueError:
        raise HTTPException(status_code=400, detail="Purpose must be 'SALE' or 'SAMPLE'.")

    # Validate all items and check available stock.
    order_items = []
    for item in items:
        barcode = item.get("barcode", "").strip()
        quantity = item.get("quantity", 0)

        if not barcode or quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid item: barcode='{barcode}', quantity={quantity}.",
            )

        product = db.query(Product).filter(Product.barcode == barcode).first()
        if not product:
            raise HTTPException(
                status_code=404,
                detail=f"Product not found: barcode '{barcode}'.",
            )

        available = product.current_stock - product.reserved_stock
        if available < quantity:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Insufficient available stock for '{product.product_name}'. "
                    f"Available: {available}, Requested: {quantity}. "
                    f"(Total: {product.current_stock}, Reserved: {product.reserved_stock})"
                ),
            )

        order_items.append({"product": product, "barcode": barcode, "quantity": quantity})

    # Create the order.
    order = Order(
        order_number=order_number,
        customer_name=customer_name,
        customer_city=customer_city,
        purpose=dispatch_purpose,
        created_by=created_by,
        status=OrderStatus.PENDING,
        idempotency_key=generate_idempotency_key(order_number),
    )
    db.add(order)
    db.flush()

    # Create items and reserve stock.
    response_items = []
    for item_data in order_items:
        product = item_data["product"]

        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            barcode=item_data["barcode"],
            quantity_ordered=item_data["quantity"],
            quantity_packed=0,
        )
        db.add(order_item)

        # Reserve stock.
        product.reserved_stock += item_data["quantity"]

        response_items.append({
            "barcode": item_data["barcode"],
            "product_name": product.product_name,
            "quantity_ordered": item_data["quantity"],
            "available_after_reserve": product.current_stock - product.reserved_stock,
        })

    db.commit()
    db.refresh(order)

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "customer_city": order.customer_city,
        "purpose": order.purpose.value,
        "created_by": order.created_by,
        "status": order.status.value,
        "items": response_items,
        "created_at": order.created_at.isoformat(),
        "message": "Order created successfully. Awaiting packing.",
    }


# ═══════════════════════════════════════════════════════════════════════
# Packer Views Pending Orders
# ═══════════════════════════════════════════════════════════════════════

@router.get("/orders/pending")
def list_pending_orders(db: Session = Depends(get_db)):
    """
    List all orders awaiting packing (PENDING and PACKING status).

    Returns order headers only — no stock counts visible to packers.
    """
    orders = (
        db.query(Order)
        .filter(Order.status.in_([OrderStatus.PENDING, OrderStatus.PACKING]))
        .order_by(Order.created_at.desc())
        .all()
    )

    result = []
    for order in orders:
        result.append({
            "order_id": order.id,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "customer_city": order.customer_city,
            "purpose": order.purpose.value,
            "created_by": order.created_by,
            "status": order.status.value,
            "total_items": len(order.items),
            "total_quantity": sum(i.quantity_ordered for i in order.items),
            "created_at": order.created_at.isoformat(),
        })

    return {"orders": result, "total_count": len(result)}


@router.get("/orders/{order_id}")
def get_order_detail(order_id: int, db: Session = Depends(get_db)):
    """
    Get full order detail including items for packing verification.

    Returns the checklist of items the packer needs to scan and verify.
    No stock counts shown — only product names, barcodes, and required quantities.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    items_detail = []
    for item in order.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        items_detail.append({
            "item_id": item.id,
            "barcode": item.barcode,
            "product_name": product.product_name if product else "Unknown",
            "category": product.category if product else "",
            "quantity_ordered": item.quantity_ordered,
            "quantity_packed": item.quantity_packed,
            "is_verified": bool(item.is_verified),
        })

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "customer_city": order.customer_city,
        "purpose": order.purpose.value,
        "created_by": order.created_by,
        "packed_by": order.packed_by,
        "status": order.status.value,
        "items": items_detail,
        "created_at": order.created_at.isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Packer Scans & Verifies an Item (Pick-to-Scan)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/orders/{order_id}/verify-item")
def verify_scanned_item(order_id: int, data: dict, db: Session = Depends(get_db)):
    """
    Verify a scanned barcode against the order's expected items.

    The packer scans a product barcode. This endpoint checks:
    1. Does this barcode belong to an item in this order?
    2. If yes: mark the item as verified with the scanned quantity.
    3. If no: return a WRONG ITEM alert.

    Body: { "barcode": "...", "quantity": 5, "packed_by": "Ramesh" }
    """
    barcode = data.get("barcode", "").strip()
    quantity = data.get("quantity", 0)
    packed_by = data.get("packed_by", "").strip()

    if not barcode:
        raise HTTPException(status_code=400, detail="Barcode is required.")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order.status in (OrderStatus.DISPATCHED, OrderStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Order is already {order.status.value}.")

    # Find matching order item.
    matching_item = None
    for item in order.items:
        if item.barcode == barcode:
            matching_item = item
            break

    if matching_item is None:
        # WRONG ITEM — this barcode is not in the order!
        product = db.query(Product).filter(Product.barcode == barcode).first()
        scanned_name = product.product_name if product else "Unknown Product"

        # List what was actually expected.
        expected = []
        for item in order.items:
            p = db.query(Product).filter(Product.id == item.product_id).first()
            expected.append(p.product_name if p else item.barcode)

        raise HTTPException(
            status_code=422,
            detail={
                "error": "WRONG_ITEM",
                "message": f"WRONG PRODUCT SCANNED! '{scanned_name}' is not in this order.",
                "scanned_barcode": barcode,
                "scanned_product": scanned_name,
                "expected_products": expected,
                "alert": "RED_BUZZER",
            },
        )

    # Correct item scanned — update packed quantity.
    matching_item.quantity_packed = quantity
    matching_item.is_verified = 1

    # Update order status.
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PACKING
    order.packed_by = packed_by

    db.commit()

    product = db.query(Product).filter(Product.id == matching_item.product_id).first()

    # Check overall completion.
    all_verified = all(bool(i.is_verified) for i in order.items)
    verified_count = sum(1 for i in order.items if i.is_verified)

    return {
        "status": "VERIFIED",
        "barcode": barcode,
        "product_name": product.product_name if product else barcode,
        "quantity_packed": quantity,
        "quantity_ordered": matching_item.quantity_ordered,
        "is_quantity_match": quantity == matching_item.quantity_ordered,
        "items_verified": verified_count,
        "items_total": len(order.items),
        "all_items_verified": all_verified,
        "message": (
            "All items verified! Ready to dispatch."
            if all_verified
            else f"Item verified ({verified_count}/{len(order.items)})."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# Complete & Dispatch Order
# ═══════════════════════════════════════════════════════════════════════

@router.post("/orders/{order_id}/dispatch")
def dispatch_order(order_id: int, data: dict, db: Session = Depends(get_db)):
    """
    Finalize and dispatch an order after all items are verified.

    Deducts physical stock and releases reserved stock.
    Only orders where all items are verified can be dispatched.

    Body: { "packed_by": "Ramesh" }
    """
    packed_by = data.get("packed_by", "").strip()

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order.status == OrderStatus.DISPATCHED:
        raise HTTPException(status_code=409, detail="Order has already been dispatched.")
    if order.status == OrderStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot dispatch a cancelled order.")

    # Verify all items are scanned.
    unverified = [i for i in order.items if not i.is_verified]
    if unverified:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(unverified)} item(s) have not been scanned and verified. "
                f"All items must be verified before dispatch."
            ),
        )

    # Deduct physical stock and release reservations.
    response_items = []
    for item in order.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            continue

        prev_stock = product.current_stock
        qty = item.quantity_packed  # Use actual packed quantity.

        # Deduct from physical stock with non-negative guard.
        result = db.execute(
            update(Product)
            .where(Product.id == product.id)
            .where(Product.current_stock >= qty)
            .values(
                current_stock=Product.current_stock - qty,
                reserved_stock=Product.reserved_stock - item.quantity_ordered,
            )
        )

        if result.rowcount == 0:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail=f"Insufficient stock for '{product.product_name}'. Please retry.",
            )

        new_stock = prev_stock - qty

        # Write immutable ledger entry.
        write_ledger_entry(
            db=db,
            product_id=product.id,
            movement_type=MovementType.DISPATCH,
            reference_number=order.order_number,
            prev_balance=prev_stock,
            delta=-qty,
            new_balance=new_stock,
            operator=packed_by or order.packed_by or "Unknown",
            notes=(
                f"{order.purpose.value} — {order.customer_name}, "
                f"{order.customer_city} — Packed by {packed_by}"
            ),
        )

        response_items.append({
            "barcode": item.barcode,
            "product_name": product.product_name,
            "quantity_dispatched": qty,
            "previous_stock": prev_stock,
            "new_stock": new_stock,
        })

    # Mark order as dispatched.
    order.status = OrderStatus.DISPATCHED
    order.packed_by = packed_by or order.packed_by
    order.packed_at = datetime.now(timezone.utc)
    db.commit()

    # Queue Google Sheets sync.
    sheets_queue.push_dispatch({
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "purpose": order.purpose.value,
        "packed_by": order.packed_by,
        "items": response_items,
    })

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "status": "DISPATCHED",
        "purpose": order.purpose.value,
        "items": response_items,
        "dispatched_at": order.packed_at.isoformat(),
        "message": "Order dispatched successfully.",
    }
