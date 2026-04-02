from datetime import datetime
from typing import Optional
from sqlalchemy import String, Float, DateTime, Text, ForeignKey, Integer, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Invoice(Base):
    """原始发票记录"""
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(50), default="image")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # NuExtract 抽取的结构化字段
    invoice_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    issue_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    seller_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    buyer_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tax_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    items_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invoice_subcategory: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # air/train/hotel/meeting/material

    # 交通类专属字段（机票/火车票/船票等）
    departure_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    arrival_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    departure_time: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    arrival_time: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_transport: Mapped[bool] = mapped_column(Boolean, default=False)

    extracted_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    extract_status: Mapped[str] = mapped_column(String(20), default="pending")

    collection_item: Mapped[Optional["CollectionItem"]] = relationship(
        "CollectionItem", back_populates="invoice", uselist=False
    )


class CollectionGroup(Base):
    """归集分组（差旅闭环 / 会议）"""
    __tablename__ = "collection_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[str] = mapped_column(String(50))
    group_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    end_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # 禁止级联删除 CollectionItem：重建行程/会议分组时只删分组记录，发票归类必须保留
    items: Mapped[list["CollectionItem"]] = relationship(
        "CollectionItem", back_populates="group"
    )


class CollectionItem(Base):
    """归集条目：关联发票与其所属分组/大类"""
    __tablename__ = "collection_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoices.id"), unique=True)
    category_id: Mapped[str] = mapped_column(String(50))
    group_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_groups.id", ondelete="SET NULL"), nullable=True
    )
    classified_by: Mapped[str] = mapped_column(String(20), default="pending")
    classified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="collection_item")
    group: Mapped[Optional["CollectionGroup"]] = relationship(
        "CollectionGroup", back_populates="items"
    )
