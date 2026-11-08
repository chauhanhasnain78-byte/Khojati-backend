"""
Khojati Inventory Management System - Admin Dashboard Router.

All endpoints in this module require admin JWT authentication.
Provides live stock counts, today's summary, audit log, low-stock
alerts, and Excel report export.
"""

import io
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import get_current_admin
from database import get_db
from models import (
    InwardItem,
    InwardReceipt,
    MovementType,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    StockLedger,
)
from schemas import (
    DashboardLiveStockResponse,
    LedgerEntryResponse,
    LiveStockItem,
    LowStockProductResponse,
    PaginatedLedgerResponse,
    TodaySummaryResponse,
)
from services.ledger import get_ledger_entries, verify_integrity
from services.reconciliation import generate_eod_snapshot, get_snapshot

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


# ═══════════════════════════════════════════════════════════════════════
# Live Stock Table
# ═══════════════════════════════════════════════════════════════════════

@router.get("/live-stock", response_model=DashboardLiveStockResponse)
def get_live_stock(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """
    Full live stock table with all product details.

    Returns every product with current stock, reserved stock, available
    stock, and low-stock status.
    """
    products = db.query(Product).order_by(Product.product_name).all()

    items = []
    low_count = 0
    for p in products:
        is_low = p.current_stock < p.low_stock_threshold
        if is_low:
            low_count += 1
        items.append(LiveStockItem(
            id=p.id,
            barcode=p.barcode,
            product_name=p.product_name,
            category=p.category,
            subcategory=p.subcategory,
            unit=p.unit,
            current_stock=p.current_stock,
            low_stock_threshold=p.low_stock_threshold,
            is_low_stock=is_low,
        ))

    return DashboardLiveStockResponse(
        products=items,
        total_products=len(items),
        low_stock_count=low_count,
    )


# ═══════════════════════════════════════════════════════════════════════
# Today's Summary
# ═══════════════════════════════════════════════════════════════════════

@router.get("/today-summary", response_model=TodaySummaryResponse)
def get_today_summary(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """
    Aggregated stock summary for today.

    Shows opening stock, total inward, total outward (split by
    sale vs sample), adjustments, closing stock, and counts of
    orders packed and GRNs received.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Total inward today.
    total_inward = (
        db.query(func.coalesce(func.sum(StockLedger.quantity_delta), 0))
        .filter(
            StockLedger.movement_type == MovementType.INWARD_GRN,
            StockLedger.created_at >= today_start,
        )
        .scalar()
    )

    # Total outward today (dispatches).
    total_outward_raw = (
        db.query(func.coalesce(func.sum(StockLedger.quantity_delta), 0))
        .filter(
            StockLedger.movement_type == MovementType.DISPATCH,
            StockLedger.created_at >= today_start,
        )
        .scalar()
    )
    total_outward = abs(int(total_outward_raw))

    # Total adjustments today.
    total_adjustments = (
        db.query(func.coalesce(func.sum(StockLedger.quantity_delta), 0))
        .filter(
            StockLedger.movement_type.in_([
                MovementType.DAMAGE_WRITEOFF,
                MovementType.RETURN_RESTOCK,
                MovementType.ADJUSTMENT,
            ]),
            StockLedger.created_at >= today_start,
        )
        .scalar()
    )

    # Current closing stock.
    closing_stock = (
        db.query(func.coalesce(func.sum(Product.current_stock), 0)).scalar()
    )

    # Opening stock = closing - inward + outward - adjustments.
    opening_stock = int(closing_stock) - int(total_inward) + total_outward - int(total_adjustments)

    # Orders packed today.
    orders_packed = (
        db.query(func.count(Order.id))
        .filter(
            Order.status == OrderStatus.DISPATCHED,
            Order.packed_at >= today_start,
        )
        .scalar()
    )

    # GRNs received today.
    grns_received = (
        db.query(func.count(InwardReceipt.id))
        .filter(InwardReceipt.created_at >= today_start)
        .scalar()
    )

    return TodaySummaryResponse(
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        opening_stock=max(0, opening_stock),
        total_inward=int(total_inward),
        total_outward=total_outward,
        total_adjustments=int(total_adjustments),
        closing_stock=int(closing_stock),
        orders_packed=int(orders_packed),
        grns_received=int(grns_received),
    )


# ═══════════════════════════════════════════════════════════════════════
# Audit Log (Paginated)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/audit-log", response_model=PaginatedLedgerResponse)
def get_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    movement_type: str = Query(None),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """
    Paginated immutable audit log with optional movement type filter.

    Every stock change is recorded here and cannot be modified or deleted.
    """
    query = db.query(StockLedger).join(Product, StockLedger.product_id == Product.id)

    if movement_type:
        try:
            mt = MovementType(movement_type)
            query = query.filter(StockLedger.movement_type == mt)
        except ValueError:
            pass  # Ignore invalid filter, return all.

    total_count = query.count()
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    entries = (
        query.order_by(StockLedger.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    response_entries = []
    for entry in entries:
        product = db.query(Product).filter(Product.id == entry.product_id).first()
        response_entries.append(LedgerEntryResponse(
            id=entry.id,
            product_id=entry.product_id,
            barcode=product.barcode if product else "",
            product_name=product.product_name if product else "Deleted Product",
            movement_type=entry.movement_type.value,
            reference_number=entry.reference_number,
            previous_balance=entry.previous_balance,
            quantity_delta=entry.quantity_delta,
            new_balance=entry.new_balance,
            operator=entry.operator,
            notes=entry.notes,
            created_at=entry.created_at,
        ))

    return PaginatedLedgerResponse(
        entries=response_entries,
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ═══════════════════════════════════════════════════════════════════════
# Low Stock Alerts
# ═══════════════════════════════════════════════════════════════════════

@router.get("/low-stock")
def get_low_stock_products(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """List products whose stock is below their low-stock threshold."""
    products = (
        db.query(Product)
        .filter(Product.current_stock < Product.low_stock_threshold)
        .order_by(Product.current_stock)
        .all()
    )

    result = []
    for p in products:
        status_text = "Out of Stock" if p.current_stock == 0 else (
            "Critical" if p.current_stock < 5 else "Low Stock"
        )
        result.append({
            "barcode": p.barcode,
            "product_name": p.product_name,
            "category": p.category,
            "current_stock": p.current_stock,
            "low_stock_threshold": p.low_stock_threshold,
            "deficit": p.low_stock_threshold - p.current_stock,
            "status": status_text,
        })

    return {"products": result, "total_count": len(result)}


# ═══════════════════════════════════════════════════════════════════════
# Excel Export
# ═══════════════════════════════════════════════════════════════════════

@router.get("/export/excel")
def export_excel_report(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """
    Download a comprehensive Excel workbook with multiple sheets:
    Live Stock, Dispatch Log, Inward Log, and Audit Trail.
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl is not installed.")

    wb = Workbook()

    # Sheet 1: Live Stock.
    ws1 = wb.active
    ws1.title = "Live Stock"
    ws1.append(["Barcode", "Product Name", "Category", "Unit", "Current Stock",
                "Reserved", "Available", "Low Threshold", "Status"])
    for p in db.query(Product).order_by(Product.product_name).all():
        available = p.current_stock - p.reserved_stock
        status_text = "In Stock" if p.current_stock >= p.low_stock_threshold else (
            "Out of Stock" if p.current_stock == 0 else "Low Stock"
        )
        ws1.append([p.barcode, p.product_name, p.category, p.unit,
                    p.current_stock, p.reserved_stock, available,
                    p.low_stock_threshold, status_text])

    # Sheet 2: Dispatch Log.
    ws2 = wb.create_sheet("Dispatch Log")
    ws2.append(["Date", "Order No.", "Customer", "City", "Purpose",
                "Packed By", "Product", "Barcode", "Qty",
                "Prev Stock", "New Stock"])
    dispatched = (
        db.query(Order)
        .filter(Order.status == OrderStatus.DISPATCHED)
        .order_by(Order.packed_at.desc())
        .all()
    )
    for order in dispatched:
        for item in order.items:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            ws2.append([
                order.packed_at.strftime("%Y-%m-%d %H:%M") if order.packed_at else "",
                order.order_number,
                order.customer_name,
                order.customer_city,
                order.purpose.value if order.purpose else "",
                order.packed_by or "",
                product.product_name if product else "",
                item.barcode,
                item.quantity_packed,
                "", "",  # Historical stock not available here.
            ])

    # Sheet 3: Audit Trail.
    ws3 = wb.create_sheet("Audit Trail")
    ws3.append(["Date", "Product", "Barcode", "Movement Type", "Reference",
                "Previous Stock", "Delta", "New Stock", "Operator", "Notes"])
    ledger_entries = (
        db.query(StockLedger)
        .order_by(StockLedger.created_at.desc())
        .limit(1000)
        .all()
    )
    for entry in ledger_entries:
        product = db.query(Product).filter(Product.id == entry.product_id).first()
        ws3.append([
            entry.created_at.strftime("%Y-%m-%d %H:%M"),
            product.product_name if product else "",
            product.barcode if product else "",
            entry.movement_type.value,
            entry.reference_number,
            entry.previous_balance,
            entry.quantity_delta,
            entry.new_balance,
            entry.operator,
            entry.notes or "",
        ])

    # Write to buffer.
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"Khojati_Inventory_Report_{date.today().isoformat()}.xlsx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ═══════════════════════════════════════════════════════════════════════
# Integrity Check
# ═══════════════════════════════════════════════════════════════════════

@router.get("/integrity-check")
def check_ledger_integrity(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """Verify that stock levels match the sum of all ledger entries."""
    return verify_integrity(db)
