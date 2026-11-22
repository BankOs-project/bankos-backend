from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
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
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    transfer_mode: Mapped[str | None] = mapped_column(String(20))
    transfer_reference: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
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


class IdempotencyReceiptModel(Base):
    """Respuesta HTTP confirmada en la misma transacción que el movimiento."""

    __tablename__ = "transaction_idempotency_receipts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int]
    body: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TransferQuoteModel(Base):
    """Autorización temporal de confirmación, privada del remitente."""

    __tablename__ = "transfer_quotes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    source_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    source_number: Mapped[str] = mapped_column(String(20))
    destination_tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    destination_public_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    destination_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    destination_owner_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    destination_number: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

