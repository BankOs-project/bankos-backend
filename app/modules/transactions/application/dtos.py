from decimal import Decimal
from datetime import datetime
from typing import Annotated, Any
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.transactions.domain.entities import OperationType, TransactionStatus, TransferMode


PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
AccountNumber = Annotated[str, Field(min_length=1, max_length=20)]


class QuoteRequestDTO(BaseModel):
    """Esquema de solicitud para pre-cotización de una operación financiera."""

    operation_type: OperationType
    source_account_number: str = Field(description="Número de cuenta origen")
    destination_account_number: AccountNumber | None = Field(
        default=None,
        description="Número de cuenta destino (requerido solo en transferencia)",
    )
    amount: PositiveAmount = Field(description="Monto a operar (estrictamente positivo)")
    transfer_mode: TransferMode | None = None
    destination_bank_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_transfer_destination(self) -> "QuoteRequestDTO":
        if self.operation_type == OperationType.TRANSFER:
            if not self.destination_account_number:
                raise ValueError("La cotización de transferencia requiere una cuenta destino")
            if self.transfer_mode == TransferMode.OTHER_BANK:
                if self.destination_bank_id is None:
                    raise ValueError("Otro banco requiere destination_bank_id")
                return self
            if self.source_account_number == self.destination_account_number:
                raise ValueError("La cuenta origen y destino no pueden ser la misma")
        elif self.transfer_mode is not None:
            raise ValueError("transfer_mode solo aplica a transferencias")
        if self.destination_bank_id is not None:
            raise ValueError("destination_bank_id solo aplica a transferencias hacia otro banco")
        return self


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


class RecipientPreviewDTO(BaseModel):
    bank_name: str
    masked_holder_name: str
    account_last_four: str


class TransferQuoteDTO(QuoteResponseDTO):
    quote_id: uuid.UUID
    expires_at: datetime
    transfer_mode: TransferMode
    recipient: RecipientPreviewDTO | None = None


class MutableTransactionDTO(BaseModel):
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=100,
        description="Campo legado opcional. Si se envía, debe coincidir con Idempotency-Key.",
    )


class DepositDTO(MutableTransactionDTO):
    """Esquema de solicitud para abono de fondos a una cuenta activa."""

    account_number: AccountNumber = Field(description="Número de cuenta donde se acreditarán los fondos")
    amount: PositiveAmount = Field(description="Monto a abonar en la divisa de la cuenta")


class WithdrawDTO(MutableTransactionDTO):
    """Esquema de solicitud para débito de fondos de una cuenta activa del titular."""

    account_number: AccountNumber = Field(description="Número de cuenta de la cual se debitarán los fondos")
    amount: PositiveAmount = Field(description="Monto a retirar en la divisa de la cuenta")


class TransferDTO(MutableTransactionDTO):
    """Esquema de solicitud para transferencia interna entre dos cuentas del mismo tenant."""
    model_config = ConfigDict(extra="forbid")

    source_account_number: AccountNumber = Field(description="Cuenta origen del débito")
    destination_account_number: AccountNumber = Field(description="Cuenta destino del crédito")
    amount: PositiveAmount = Field(description="Monto base a transferir en la divisa de la cuenta origen")

    @field_validator("destination_account_number")
    @classmethod
    def source_and_dest_must_differ(cls, v: str, info: Any) -> str:
        source = info.data.get("source_account_number")
        if source and v == source:
            raise ValueError("La cuenta origen y destino no pueden ser la misma")
        return v


class ConfirmTransferDTO(MutableTransactionDTO):
    model_config = ConfigDict(extra="forbid")

    quote_id: uuid.UUID


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
    failure_code: str | None = None
    transfer_mode: TransferMode | None = None
    transfer_reference: uuid.UUID | None = None


class PaginatedTransactionsResponseDTO(BaseModel):
    """Respuesta paginada de historial de transacciones."""

    total: int
    skip: int
    limit: int
    items: list[TransactionResponseDTO]


class CurrencySummaryDTO(BaseModel):
    currency: str
    available_balance: Decimal
    income: Decimal
    expenses: Decimal


class FinancialSummaryDTO(BaseModel):
    account_number: str | None
    start_date: datetime
    end_date: datetime
    currencies: list[CurrencySummaryDTO]

