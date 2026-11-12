from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TransactionModel(Base):
    """Modelo ORM inmutable que registra cada operación financiera en BankOS."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    operation_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    source_account_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )
    destination_account_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )
    original_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )
    original_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    converted_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )
    destination_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    exchange_rate_applied: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        default=Decimal("1.000000"),
    )
    fee_charged: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        default=Decimal("0.0000"),
    )
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="SUCCESS",
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    correlation_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("ix_tx_tenant_idempotency", "tenant_id", "idempotency_key", unique=True),
        Index("ix_tx_tenant_created", "tenant_id", "created_at"),
        Index("ix_tx_tenant_source_acc", "tenant_id", "source_account_number"),
        Index("ix_tx_tenant_dest_acc", "tenant_id", "destination_account_number"),
    )

