"""
Khojati Inventory Management System - Pydantic Schemas.

Request and response models for all API endpoints.  All models use
Pydantic v2 with strict validation rules (positive quantities, non-empty
strings, barcode format checks).
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ═══════════════════════════════════════════════════════════════════════
# Authentication
# ═══════════════════════════════════════════════════════════════════════

class PinVerifyRequest(BaseModel):
    """Request body for admin PIN verification."""
    pin: str = Field(
        ...,
        min_length=4,
        max_length=10,
        description="Admin PIN code",
    )


class TokenResponse(BaseModel):
    """JWT access token returned after successful PIN verification."""
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int = 60


# ═══════════════════════════════════════════════════════════════════════
# Products
# ═══════════════════════════════════════════════════════════════════════

class ProductCreate(BaseModel):
    """Request body for creating or updating a product."""
    barcode: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9\-]+$",
        description="Unique product barcode (alphanumeric and hyphens only)",
    )
    product_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Full product name",
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Product category",
    )
    subcategory: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Product subcategory",
    )
    unit: str = Field(
        default="pcs",
        min_length=1,
        max_length=30,
        description="Unit of measurement",
    )
    current_stock: int = Field(
        default=0,
        ge=0,
        description="Initial stock quantity (must be non-negative)",
    )
    low_stock_threshold: int = Field(
        default=20,
        ge=0,
        description="Alert threshold for low stock",
    )


class ProductResponse(BaseModel):
    """Full product detail returned for admin views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    barcode: str
    product_name: str
    category: str
    subcategory: Optional[str] = None
    unit: str
    current_stock: int
    low_stock_threshold: int
    created_at: datetime
    updated_at: datetime


class ProductPublicResponse(BaseModel):
    """
    Limited product info for non-admin endpoints.

    Stock counts are intentionally excluded from public-facing responses.
    """
    model_config = ConfigDict(from_attributes=True)

    barcode: str
    product_name: str
    category: str
    subcategory: Optional[str] = None
    unit: str


class ProductListResponse(BaseModel):
    """Paginated list of products (public view, no stock data)."""
    products: List[ProductPublicResponse]
    total_count: int


# ═══════════════════════════════════════════════════════════════════════
# Dispatch Orders
# ═══════════════════════════════════════════════════════════════════════

class DispatchItem(BaseModel):
    """Single line item in a dispatch order."""
    barcode: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Product barcode",
    )
    quantity: int = Field(
        ...,
        gt=0,
        description="Quantity to dispatch (must be greater than zero)",
    )


class DispatchOrderRequest(BaseModel):
    """Request body for submitting a packed dispatch order."""
    order_number: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique order number",
    )
    customer_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Customer full name",
    )
    customer_city: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Destination city",
    )
    packed_by: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the person who packed the order",
    )
    items: List[DispatchItem] = Field(
        ...,
        min_length=1,
        description="List of items to dispatch (at least one required)",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Optional client-generated idempotency key",
    )

    @field_validator("items")
    @classmethod
    def validate_no_duplicate_barcodes(cls, items: List[DispatchItem]) -> List[DispatchItem]:
        """Reject orders containing duplicate barcodes."""
        barcodes = [item.barcode for item in items]
        if len(barcodes) != len(set(barcodes)):
            raise ValueError(
                "Duplicate barcodes found in the order. "
                "Combine quantities for the same product into a single line item."
            )
        return items


class DispatchOrderItemResponse(BaseModel):
    """Response detail for a single dispatched item."""
    barcode: str
    product_name: str
    quantity: int
    previous_stock: int
    new_stock: int


class DispatchOrderResponse(BaseModel):
    """Confirmation response after a successful dispatch."""
    order_id: int
    order_number: str
    customer_name: str
    customer_city: str
    packed_by: str
    status: str
    idempotency_key: str
    items: List[DispatchOrderItemResponse]
    created_at: datetime
    message: str = "Order dispatched successfully."


# ═══════════════════════════════════════════════════════════════════════
# Inward Goods Receipt
# ═══════════════════════════════════════════════════════════════════════

class InwardItem(BaseModel):
    """Single line item in an inward receipt."""
    barcode: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Product barcode",
    )
    quantity: int = Field(
        ...,
        gt=0,
        description="Quantity received (must be greater than zero)",
    )


class InwardReceiptRequest(BaseModel):
    """Request body for receiving inward stock."""
    grn_number: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique Goods Receipt Note number",
    )
    challan_number: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Supplier challan/invoice number",
    )
    supplier_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Supplier name",
    )
    received_by: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the person receiving the goods",
    )
    items: List[InwardItem] = Field(
        ...,
        min_length=1,
        description="List of items received (at least one required)",
    )


class InwardReceiptItemResponse(BaseModel):
    """Response detail for a single received item."""
    barcode: str
    product_name: str
    quantity: int
    previous_stock: int
    new_stock: int


class InwardReceiptResponse(BaseModel):
    """Confirmation response after successful inward receipt."""
    receipt_id: int
    grn_number: str
    challan_number: str
    supplier_name: str
    received_by: str
    items: List[InwardReceiptItemResponse]
    created_at: datetime
    message: str = "Inward stock received successfully."


# ═══════════════════════════════════════════════════════════════════════
# Stock Adjustments
# ═══════════════════════════════════════════════════════════════════════

class AdjustmentRequest(BaseModel):
    """Request body for manual stock adjustments (damage write-off or return)."""
    barcode: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Product barcode",
    )
    quantity_delta: int = Field(
        ...,
        description=(
            "Stock change: positive for restocking returns, "
            "negative for damage write-offs"
        ),
    )
    movement_type: str = Field(
        ...,
        description="Must be 'DAMAGE_WRITEOFF' or 'RETURN_RESTOCK'",
    )
    operator: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the person performing the adjustment",
    )
    notes: str = Field(
        default="",
        max_length=500,
        description="Reason or notes for the adjustment",
    )

    @field_validator("movement_type")
    @classmethod
    def validate_movement_type(cls, value: str) -> str:
        """Only DAMAGE_WRITEOFF and RETURN_RESTOCK are valid adjustment types."""
        allowed = {"DAMAGE_WRITEOFF", "RETURN_RESTOCK"}
        if value not in allowed:
            raise ValueError(
                f"Invalid movement type '{value}'. "
                f"Allowed values: {', '.join(sorted(allowed))}."
            )
        return value

    @field_validator("quantity_delta")
    @classmethod
    def validate_quantity_delta_nonzero(cls, value: int) -> int:
        """Delta must not be zero."""
        if value == 0:
            raise ValueError("Quantity delta must not be zero.")
        return value


class AdjustmentResponse(BaseModel):
    """Confirmation response after a stock adjustment."""
    barcode: str
    product_name: str
    movement_type: str
    quantity_delta: int
    previous_stock: int
    new_stock: int
    operator: str
    notes: str
    message: str = "Stock adjustment applied successfully."


# ═══════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════

class LiveStockItem(BaseModel):
    """Single row in the live stock dashboard table."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    barcode: str
    product_name: str
    category: str
    subcategory: Optional[str] = None
    unit: str
    current_stock: int
    low_stock_threshold: int
    is_low_stock: bool


class DashboardLiveStockResponse(BaseModel):
    """Full live stock table for the admin dashboard."""
    products: List[LiveStockItem]
    total_products: int
    low_stock_count: int


class TodaySummaryResponse(BaseModel):
    """Aggregated stock summary for today."""
    date: str
    opening_stock: int
    total_inward: int
    total_outward: int
    total_adjustments: int
    closing_stock: int
    orders_packed: int
    grns_received: int


class LedgerEntryResponse(BaseModel):
    """Single audit ledger entry."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    barcode: str
    product_name: str
    movement_type: str
    reference_number: str
    previous_balance: int
    quantity_delta: int
    new_balance: int
    operator: str
    notes: Optional[str]
    created_at: datetime


class PaginatedLedgerResponse(BaseModel):
    """Paginated ledger entries for the audit log."""
    entries: List[LedgerEntryResponse]
    total_count: int
    page: int
    page_size: int
    total_pages: int


class LowStockProductResponse(BaseModel):
    """Product that has fallen below its low stock threshold."""
    model_config = ConfigDict(from_attributes=True)

    barcode: str
    product_name: str
    category: str
    current_stock: int
    low_stock_threshold: int
    deficit: int


# ═══════════════════════════════════════════════════════════════════════
# Google Sheets Sync
# ═══════════════════════════════════════════════════════════════════════

class SyncStatusResponse(BaseModel):
    """Current status of the Google Sheets sync queue."""
    queue_length: int
    last_sync_at: Optional[str]
    is_configured: bool
    message: str


class SyncTriggerResponse(BaseModel):
    """Response after triggering a manual sync."""
    triggered: bool
    queue_length: int
    message: str
