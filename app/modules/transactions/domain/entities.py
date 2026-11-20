from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import uuid


class OperationType(str, Enum):
    """Tipos de operación financiera soportados en BankOS."""
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRANSFER = "TRANSFER"


class TransactionStatus(str, Enum):
    """Estado de la transacción persistida."""
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


@dataclass
class Transaction:
    """Entidad de dominio inmutable que registra una operación financiera en BankOS."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    operation_type: OperationType
    source_account_number: str | None
    destination_account_number: str | None
    original_amount: Decimal
    original_currency: str
    converted_amount: Decimal
    destination_currency: str
    exchange_rate_applied: Decimal
    fee_charged: Decimal
    status: TransactionStatus
    idempotency_key: str
    correlation_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor_user_id: uuid.UUID | None = None
    failure_code: str | None = None

    @classmethod
    def create(
        cls,
        tenant_id: uuid.UUID,
        operation_type: OperationType,
        original_amount: Decimal,
        original_currency: str,
        converted_amount: Decimal,
        destination_currency: str,
        exchange_rate_applied: Decimal,
        fee_charged: Decimal,
        idempotency_key: str,
        correlation_id: str,
        source_account_number: str | None = None,
        destination_account_number: str | None = None,
        status: TransactionStatus = TransactionStatus.SUCCESS,
        actor_user_id: uuid.UUID | None = None,
        failure_code: str | None = None,
    ) -> "Transaction":
        return cls(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            operation_type=operation_type,
            source_account_number=source_account_number,
            destination_account_number=destination_account_number,
            original_amount=Decimal(str(original_amount)),
            original_currency=original_currency.upper(),
            converted_amount=Decimal(str(converted_amount)),
            destination_currency=destination_currency.upper(),
            exchange_rate_applied=Decimal(str(exchange_rate_applied)),
            fee_charged=Decimal(str(fee_charged)),
            status=status,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            created_at=datetime.now(timezone.utc),
            actor_user_id=actor_user_id,
            failure_code=failure_code,
        )


@dataclass
class TransactionQuote:
    """Resultado de la pre-cotización de una operación financiera sin modificar saldos."""

    operation_type: OperationType
    source_currency: str
    destination_currency: str
    original_amount: Decimal
    fee_calculated: Decimal
    amount_to_debit: Decimal
    exchange_rate_applied: Decimal
    final_amount_to_receive: Decimal

