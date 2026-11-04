"""
Khojati Inventory Management System - Stock Inward Router.

Endpoint for receiving incoming stock from suppliers/factory.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import Product
from schemas import InwardReceiptRequest, InwardReceiptResponse
from services.stock_engine import (
    ProductNotFoundError,
    DuplicateOrderError,
    StockEngineError,
    receive_inward,
)

router = APIRouter(prefix="/api", tags=["Inward"])


@router.post("/inward", response_model=InwardReceiptResponse)
def submit_inward_receipt(
    data: InwardReceiptRequest,
    db: Session = Depends(get_db),
):
    """
    Record incoming stock from a supplier or factory.

    Creates an inward receipt (GRN) and adds the received quantities
    to the product stock levels.
    """
    try:
        return receive_inward(db, data)
    except DuplicateOrderError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except StockEngineError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
