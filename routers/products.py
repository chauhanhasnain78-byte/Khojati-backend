"""
Khojati Inventory Management System - Product Management Router.

Endpoints for product catalogue CRUD operations.
Public endpoints return product name and barcode only (no stock data).
Admin endpoints require JWT authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth import get_current_admin
from database import get_db
from models import Product
from schemas import (
    ProductCreate,
    ProductListResponse,
    ProductPublicResponse,
    ProductResponse,
)

router = APIRouter(prefix="/api", tags=["Products"])


# ── Public Endpoints (No stock data exposed) ──────────────────────────

@router.get("/products", response_model=ProductListResponse)
def list_products(db: Session = Depends(get_db)):
    """
    List all products with name, barcode, and category only.

    Stock counts are intentionally excluded to prevent floor workers
    from seeing total inventory levels.
    """
    products = db.query(Product).order_by(Product.product_name).all()
    return ProductListResponse(
        products=[
            ProductPublicResponse.model_validate(p) for p in products
        ],
        total_count=len(products),
    )


@router.get("/product/{barcode}", response_model=ProductPublicResponse)
def lookup_product(barcode: str, db: Session = Depends(get_db)):
    """
    Look up a single product by barcode.

    Returns name and category only — no stock counts.
    Used by the scanner app to identify a scanned barcode.
    """
    product = db.query(Product).filter(Product.barcode == barcode).first()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No product found with barcode '{barcode}'.",
        )
    return ProductPublicResponse.model_validate(product)


# ── Admin Endpoints (JWT required) ────────────────────────────────────

@router.post("/admin/products", response_model=ProductResponse)
def create_or_update_product(
    data: ProductCreate,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """
    Create a new product or update an existing one by barcode.

    If a product with the given barcode already exists, its name,
    category, unit, and threshold are updated.  Stock is only set
    on initial creation.
    """
    existing = db.query(Product).filter(Product.barcode == data.barcode).first()

    if existing:
        existing.product_name = data.product_name
        existing.category = data.category
        existing.unit = data.unit
        existing.low_stock_threshold = data.low_stock_threshold
        db.commit()
        db.refresh(existing)
        return ProductResponse.model_validate(existing)

    product = Product(
        barcode=data.barcode,
        product_name=data.product_name,
        category=data.category,
        unit=data.unit,
        current_stock=data.current_stock,
        low_stock_threshold=data.low_stock_threshold,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return ProductResponse.model_validate(product)


@router.get("/admin/products", response_model=list[ProductResponse])
def list_products_admin(
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """List all products with full details including stock (admin only)."""
    return [
        ProductResponse.model_validate(p)
        for p in db.query(Product).order_by(Product.product_name).all()
    ]


@router.delete("/admin/products/{product_id}")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    """Delete a product from the catalogue (admin only)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )
    db.delete(product)
    db.commit()
    return {"message": f"Product '{product.product_name}' deleted successfully."}
