"""
Khojati Inventory Management System - Stock Adjustments Router.

Endpoints for damage write-offs (with photo proof and admin approval)
and return restocking.
"""

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from auth import get_current_admin
from database import get_db
from models import DamageReport, MovementType, Product
from schemas import AdjustmentRequest, AdjustmentResponse
from services.ledger import write_ledger_entry
from services.sheets_queue import sheets_queue

router = APIRouter(prefix="/api", tags=["Adjustments"])

import tempfile

if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "khojati_uploads", "damage_photos")
else:
    UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads", "damage_photos")

try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════════════
# Worker Reports Damage (With Photo Proof)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/damage/report")
async def report_damage(
    barcode: str = Form(...),
    quantity: int = Form(...),
    reported_by: str = Form(...),
    reason: str = Form(""),
    order_id: int = Form(None),
    photo: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    """
    Worker reports damaged goods with mandatory photo evidence.

    The report is created with PENDING status. Stock is NOT deducted
    until an admin reviews the photo and approves the report.
    """
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")

    product = db.query(Product).filter(Product.barcode == barcode).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product not found: barcode '{barcode}'.")

    # Save photo if provided (stores as data-URI for cloud/Vercel persistence, plus local disk backup)
    photo_path = None
    if photo:
        import base64
        content = await photo.read()
        mime = photo.content_type or "image/jpeg"
        b64 = base64.b64encode(content).decode("utf-8")
        photo_path = f"data:{mime};base64,{b64}"
        try:
            ext = os.path.splitext(photo.filename or "img.jpg")[1]
            filename = f"{uuid.uuid4().hex}{ext}"
            local_file = os.path.join(UPLOAD_DIR, filename)
            with open(local_file, "wb") as f:
                f.write(content)
        except Exception:
            pass

    report = DamageReport(
        product_id=product.id,
        barcode=barcode,
        quantity=quantity,
        reported_by=reported_by,
        photo_path=photo_path,
        reason=reason,
        status="PENDING",
        order_id=order_id,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return {
        "report_id": report.id,
        "barcode": barcode,
        "product_name": product.product_name,
        "quantity": quantity,
        "reported_by": reported_by,
        "status": "PENDING",
        "photo_saved": photo_path is not None,
        "message": "Damage report submitted. Awaiting admin approval.",
    }


# ═══════════════════════════════════════════════════════════════════════
# Admin Reviews and Approves/Rejects Damage Reports
# ═══════════════════════════════════════════════════════════════════════

@router.get("/admin/damage/pending")
def list_pending_damage_reports(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """List all damage reports awaiting admin review."""
    reports = db.query(DamageReport).filter(DamageReport.status == "PENDING").all()

    result = []
    for r in reports:
        product = db.query(Product).filter(Product.id == r.product_id).first()
        result.append({
            "report_id": r.id,
            "barcode": r.barcode,
            "product_name": product.product_name if product else "Unknown",
            "quantity": r.quantity,
            "reported_by": r.reported_by,
            "reason": r.reason,
            "photo_path": r.photo_path,
            "order_id": r.order_id,
            "created_at": r.created_at.isoformat(),
        })

    return {"reports": result, "total_count": len(result)}


@router.post("/admin/damage/{report_id}/approve")
def approve_damage_report(
    report_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """
    Approve a damage report and deduct stock.

    Only upon approval is the stock actually reduced. This prevents
    workers from falsely claiming breakage to steal inventory.
    """
    report = db.query(DamageReport).filter(DamageReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Damage report not found.")
    if report.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Report is already {report.status}.")

    product = db.query(Product).filter(Product.id == report.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Associated product not found.")

    prev_stock = product.current_stock
    if prev_stock < report.quantity:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot deduct {report.quantity} units. "
                f"Current stock: {prev_stock}."
            ),
        )

    # Deduct stock.
    product.current_stock -= report.quantity
    new_stock = product.current_stock

    # Write immutable ledger entry.
    write_ledger_entry(
        db=db,
        product_id=product.id,
        movement_type=MovementType.DAMAGE_WRITEOFF,
        reference_number=f"DMG-{report.id}",
        prev_balance=prev_stock,
        delta=-report.quantity,
        new_balance=new_stock,
        operator=admin.get("sub", "Admin"),
        notes=f"Damage approved. Reported by {report.reported_by}. Reason: {report.reason}",
    )

    # Update report status.
    report.status = "APPROVED"
    report.approved_by = admin.get("sub", "Admin")
    report.approved_at = datetime.now(timezone.utc)

    db.commit()

    sheets_queue.push_stock_update()

    return {
        "report_id": report.id,
        "status": "APPROVED",
        "product_name": product.product_name,
        "quantity_deducted": report.quantity,
        "previous_stock": prev_stock,
        "new_stock": new_stock,
        "message": "Damage report approved. Stock has been deducted.",
    }


@router.post("/admin/damage/{report_id}/reject")
def reject_damage_report(
    report_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """Reject a damage report. No stock is deducted."""
    report = db.query(DamageReport).filter(DamageReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Damage report not found.")
    if report.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Report is already {report.status}.")

    report.status = "REJECTED"
    db.commit()

    return {
        "report_id": report.id,
        "status": "REJECTED",
        "message": "Damage report rejected. No stock deducted.",
    }


# ═══════════════════════════════════════════════════════════════════════
# Generic Stock Adjustment (Admin Only — Returns, Corrections)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/adjustment", response_model=AdjustmentResponse)
def adjust_stock(
    data: AdjustmentRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """Apply a manual stock adjustment (admin only)."""
    from services.stock_engine import (
        InsufficientStockError,
        ProductNotFoundError,
        StockEngineError,
        adjust_stock as engine_adjust,
    )

    try:
        return engine_adjust(db, data)
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InsufficientStockError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except StockEngineError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
