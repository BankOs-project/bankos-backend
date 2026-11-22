from datetime import datetime
from decimal import Decimal
import uuid
from pydantic import BaseModel, Field, field_validator

from app.modules.accounts.domain.entities import AccountStatus


class CreateAccountDTO(BaseModel):
    """Esquema de solicitud para apertura de cuenta bancaria."""

    currency: str = Field(default="COP", min_length=3, max_length=3, description="Divisa de la cuenta: COP, USD, EUR")
    initial_deposit: Decimal = Field(default=Decimal("0.0000"), ge=0, description="Monto inicial de apertura")
    owner_id: uuid.UUID | None = Field(default=None, description="ID del titular (requerido si el admin crea la cuenta para un cliente)")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        curr = v.upper()
        if curr not in ("COP", "USD", "EUR"):
            raise ValueError("La divisa debe ser COP, USD o EUR")
        return curr


class AccountResponseDTO(BaseModel):
    """Esquema de respuesta para cuentas bancarias."""

    id: uuid.UUID
    account_number: str
    owner_id: uuid.UUID
    tenant_id: uuid.UUID
    balance: Decimal
    currency: str
    status: AccountStatus
    created_at: datetime
    updated_at: datetime
    reactivated_at: datetime | None = None
    can_deactivate_at: datetime | None = None
    requires_admin_reactivation: bool = False


class PaginatedAccountsResponseDTO(BaseModel):
    """Esquema para respuestas paginadas de cuentas bancarias."""

    total: int
    skip: int
    limit: int
    items: list[AccountResponseDTO]

