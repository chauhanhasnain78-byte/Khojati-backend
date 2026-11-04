"""
Khojati Inventory Management System - SQLAlchemy ORM Models.

Defines all database tables with strict constraints, CHECK guards,
and relationships for the inventory management system.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# ═══════════════════════════════════════════════════════════════════════
# Enumerations
# ═══════════════════════════════════════════════════════════════════════

class OrderStatus(enum.Enum):
    """Lifecycle status of a dispatch order."""
    PENDING = "PENDING"
    PACKING = "PACKING"
    PACKED = "PACKED"
    DISPATCHED = "DISPATCHED"
    PARTIALLY_PACKED = "PARTIALLY_PACKED"
    CANCELLED = "CANCELLED"


class DispatchPurpose(enum.Enum):
    """Purpose of the dispatch — Sale or Sample."""
    SALE = "SALE"
    SAMPLE = "SAMPLE"


class MovementType(enum.Enum):
    """Category of stock movement for the immutable ledger."""
    DISPATCH = "DISPATCH"
    INWARD_GRN = "INWARD_GRN"
    DAMAGE_WRITEOFF = "DAMAGE_WRITEOFF"
    RETURN_RESTOCK = "RETURN_RESTOCK"
    ADJUSTMENT = "ADJUSTMENT"
    ORDER_RESERVED = "ORDER_RESERVED"
    RESERVATION_RELEASED = "RESERVATION_RELEASED"


def _utcnow():
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════
# Product Master
# ═══════════════════════════════════════════════════════════════════════

class Product(Base):
    """Product master catalogue with live stock tracking."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    barcode = Column(String(50), unique=True, nullable=False, index=True)
    product_name = Column(String(200), nullable=False)
    category = Column(String(100), nullable=False, default="General")
    unit = Column(String(30), nullable=False, default="pcs")
    current_stock = Column(Integer, nullable=False, default=0)
    reserved_stock = Column(Integer, nullable=False, default=0)
    low_stock_threshold = Column(Integer, nullable=False, default=20)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("current_stock >= 0", name="ck_product_nonnegative_stock"),
        CheckConstraint("reserved_stock >= 0", name="ck_product_nonnegative_reserved"),
    )

    # Relationships
    order_items = relationship("OrderItem", back_populates="product")
    inward_items = relationship("InwardItem", back_populates="product")
    ledger_entries = relationship("StockLedger", back_populates="product")

    @property
    def available_stock(self) -> int:
        """Stock available to sell (physical minus reserved)."""
        return max(0, self.current_stock - self.reserved_stock)

    @property
    def is_low_stock(self) -> bool:
        """Whether the available stock is below the alert threshold."""
        return self.available_stock < self.low_stock_threshold

    def __repr__(self):
        return f"<Product(barcode={self.barcode!r}, name={self.product_name!r}, stock={self.current_stock})>"


# ═══════════════════════════════════════════════════════════════════════
# Orders (Created by Sir → Packed by Worker)
# ═══════════════════════════════════════════════════════════════════════

class Order(Base):
    """
    Dispatch order header.

    Orders are created by Sir (admin) with product items and quantities.
    Workers on the floor pick orders from the pending queue and scan
    barcodes to verify each item before dispatch.
    """
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_number = Column(String(100), unique=True, nullable=False, index=True)
    customer_name = Column(String(200), nullable=False)
    customer_city = Column(String(200), nullable=False, default="")
    purpose = Column(
        Enum(DispatchPurpose),
        nullable=False,
        default=DispatchPurpose.SALE,
    )
    created_by = Column(String(100), nullable=False, default="Admin")
    packed_by = Column(String(100), nullable=True)
    status = Column(
        Enum(OrderStatus),
        nullable=False,
        default=OrderStatus.PENDING,
    )
    idempotency_key = Column(String(64), unique=True, nullable=True)
    notes = Column(Text, nullable=True, default="")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    packed_at = Column(DateTime, nullable=True)

    # Relationships
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Order(number={self.order_number!r}, status={self.status.value})>"


class OrderItem(Base):
    """Individual line item within a dispatch order."""
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    barcode = Column(String(50), nullable=False)
    quantity_ordered = Column(Integer, nullable=False)
    quantity_packed = Column(Integer, nullable=False, default=0)
    is_verified = Column(Integer, nullable=False, default=0)  # 0=False, 1=True

    __table_args__ = (
        CheckConstraint("quantity_ordered > 0", name="ck_orderitem_positive_qty"),
        CheckConstraint("quantity_packed >= 0", name="ck_orderitem_nonneg_packed"),
    )

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


# ═══════════════════════════════════════════════════════════════════════
# Inward Goods Receipt
# ═══════════════════════════════════════════════════════════════════════

class InwardReceipt(Base):
    """Goods Receipt Note header for incoming stock from suppliers."""
    __tablename__ = "inward_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    grn_number = Column(String(100), unique=True, nullable=False, index=True)
    challan_number = Column(String(100), nullable=False)
    supplier_name = Column(String(200), nullable=False)
    received_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    # Relationships
    items = relationship("InwardItem", back_populates="receipt", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<InwardReceipt(grn={self.grn_number!r})>"


class InwardItem(Base):
    """Individual line item in an inward goods receipt."""
    __tablename__ = "inward_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_id = Column(Integer, ForeignKey("inward_receipts.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    barcode = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_inwarditem_positive_qty"),
    )

    # Relationships
    receipt = relationship("InwardReceipt", back_populates="items")
    product = relationship("Product", back_populates="inward_items")


# ═══════════════════════════════════════════════════════════════════════
# Damage Reports (Photo Proof + Sir Approval)
# ═══════════════════════════════════════════════════════════════════════

class DamageReport(Base):
    """
    Damage report requiring admin photo verification and approval.

    Workers capture a photo of damaged goods. The report remains
    PENDING until an admin approves or rejects it.  Only approved
    reports trigger a stock deduction.
    """
    __tablename__ = "damage_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    barcode = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    reported_by = Column(String(100), nullable=False)
    photo_path = Column(String(500), nullable=True)
    reason = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING / APPROVED / REJECTED
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_damage_positive_qty"),
    )

    # Relationships
    product = relationship("Product")
    order = relationship("Order")


# ═══════════════════════════════════════════════════════════════════════
# Immutable Stock Ledger (Audit Trail)
# ═══════════════════════════════════════════════════════════════════════

class StockLedger(Base):
    """
    Append-only immutable audit trail for all stock movements.

    No UPDATE or DELETE operations are permitted on this table.
    Every stock change — dispatch, inward, damage, return — creates
    a new row capturing the before/after state.
    """
    __tablename__ = "stock_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    movement_type = Column(Enum(MovementType), nullable=False)
    reference_number = Column(String(200), nullable=False)
    previous_balance = Column(Integer, nullable=False)
    quantity_delta = Column(Integer, nullable=False)
    new_balance = Column(Integer, nullable=False)
    operator = Column(String(100), nullable=False)
    notes = Column(Text, nullable=True, default="")
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    # Relationships
    product = relationship("Product", back_populates="ledger_entries")

    def __repr__(self):
        return (
            f"<StockLedger(product_id={self.product_id}, "
            f"type={self.movement_type.value}, delta={self.quantity_delta:+d})>"
        )


# ═══════════════════════════════════════════════════════════════════════
# End-of-Day Snapshots
# ═══════════════════════════════════════════════════════════════════════

class EODSnapshot(Base):
    """
    Daily stock snapshot for reconciliation and historical reporting.

    Stores a JSON blob containing per-product stock levels as captured
    at end-of-day.  Only one snapshot per calendar date is permitted.
    """
    __tablename__ = "eod_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String(10), unique=True, nullable=False)
    product_snapshots = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    def __repr__(self):
        return f"<EODSnapshot(date={self.snapshot_date!r})>"
