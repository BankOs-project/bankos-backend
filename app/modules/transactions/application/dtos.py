from decimal import Decimal
from datetime import datetime
from typing import Any
import uuid
from pydantic import BaseModel, Field, field_validator

from app.modules.transactions.domain.entities import OperationType, TransactionStatus


class QuoteRequestDTO(BaseModel):
    """Esquema de solicitud para pre-cotización de una operación financiera."""

    operation_type: OperationType
    source_account_number: str = Field(description="Número de cuenta origen")
    destination_account_number: str | None = Field(
        default=None,
        description="Número de cuenta destino (requerido solo en transferencia)",
    )
    amount: Decimal = Field(gt=0, description="Monto a operar (estrictamente positivo)")


class QuoteResponseDTO(BaseModel):
    """Desglose detallado de la pre-cotización sin modificar saldos."""

    operation_type: OperationType
    source_currency: str
    destination_currency: str
    original_amount: Decimal
    fee_calculated: Decimal
    amount_to_debit: Decimal
    exchange_rate_applied: Decimal
    final_amount_to_receive: Decimal


class DepositDTO(BaseModel):
    """Esquema de solicitud para abono de fondos a una cuenta activa."""

    account_number: str = Field(description="Número de cuenta donde se acreditarán los fondos")
    amount: Decimal = Field(gt=0, description="Monto a abonar en la divisa de la cuenta")
    idempotency_key: str = Field(min_length=1, max_length=100, description="Clave UUID de idempotencia (cliente)")


class WithdrawDTO(BaseModel):
    """Esquema de solicitud para débito de fondos de una cuenta activa del titular."""

    account_number: str = Field(description="Número de cuenta de la cual se debitarán los fondos")
    amount: Decimal = Field(gt=0, description="Monto a retirar en la divisa de la cuenta")
    idempotency_key: str = Field(min_length=1, max_length=100, description="Clave UUID de idempotencia (cliente)")


class TransferDTO(BaseModel):
    """Esquema de solicitud para transferencia interna entre dos cuentas del mismo tenant."""

    source_account_number: str = Field(description="Cuenta origen del débito")
    destination_account_number: str = Field(description="Cuenta destino del crédito")
    amount: Decimal = Field(gt=0, description="Monto base a transferir en la divisa de la cuenta origen")
    idempotency_key: str = Field(min_length=1, max_length=100, description="Clave UUID de idempotencia (cliente)")

    @field_validator("destination_account_number")
    @classmethod
    def source_and_dest_must_differ(cls, v: str, info: Any) -> str:
        source = info.data.get("source_account_number")
        if source and v == source:
            raise ValueError("La cuenta origen y destino no pueden ser la misma")
        return v


class TransactionResponseDTO(BaseModel):
    """Esquema de respuesta completo para una transacción persistida."""

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
    created_at: datetime


class PaginatedTransactionsResponseDTO(BaseModel):
    """Respuesta paginada de historial de transacciones."""

    total: int
    skip: int
    limit: int
    items: list[TransactionResponseDTO]

