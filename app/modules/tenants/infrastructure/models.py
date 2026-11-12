from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid
from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TenantModel(Base):
    """Modelo ORM para la persistencia de Tenants institucionales en PostgreSQL."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    max_transaction_limit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )
    fee_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="FIXED",
    )
    fee_value: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )
    base_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="COP",
    )
    exchange_rates: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    webhook_url: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

