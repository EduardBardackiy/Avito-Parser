from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.db import Base


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    price: Mapped[str] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ParsedListing(Base):
    __tablename__ = "parsed_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=True)

    # Legacy text fields (kept for backward compatibility)
    price: Mapped[str] = mapped_column(String(128), nullable=True)
    bail: Mapped[str] = mapped_column(String(128), nullable=True)
    tax: Mapped[str] = mapped_column(String(128), nullable=True)
    services: Mapped[str] = mapped_column(String(256), nullable=True)

    # Enhanced parsed fields
    price_raw: Mapped[str] = mapped_column(String(128), nullable=True)
    price_value: Mapped[int] = mapped_column(Integer, nullable=True)
    bail_raw: Mapped[str] = mapped_column(String(128), nullable=True)
    bail_value: Mapped[int] = mapped_column(Integer, nullable=True)
    commission_raw: Mapped[str] = mapped_column(String(128), nullable=True)
    commission: Mapped[int] = mapped_column(Integer, nullable=True)
    services_raw: Mapped[str] = mapped_column(String(256), nullable=True)

    address: Mapped[str] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    images_json: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


