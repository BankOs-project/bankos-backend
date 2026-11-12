from datetime import datetime
from decimal import Decimal
from typing import Any
import uuid
from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.modules.tenants.domain.entities import FeeType


class CreateTenantDTO(BaseModel):
    """Esquema de solicitud para el registro de un nuevo tenant institucional."""

    name: str = Field(min_length=2, max_length=150, description="Nombre de la institución financiera")
    slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$", description="Identificador único en minúsculas")
    max_transaction_limit: Decimal = Field(gt=0, description="Límite máximo por transacción")
    fee_type: FeeType = Field(description="Tipo de comisión: FIXED o PERCENTAGE")
    fee_value: Decimal = Field(ge=0, description="Valor o porcentaje de comisión")
    base_currency: str = Field(default="COP", min_length=3, max_length=3, description="Divisa base (ej: COP, USD, EUR)")
    exchange_rates: dict[str, Any] = Field(default_factory=dict, description="Tabla estática de tasas de conversión")
    webhook_url: str | None = Field(default=None, description="URL para notificaciones de eventos críticos")

    @field_validator("base_currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        currency = v.upper()
        if currency not in ("COP", "USD", "EUR"):
            raise ValueError("La divisa base debe ser COP, USD o EUR")
        return currency


class UpdateTenantConfigDTO(BaseModel):
    """Esquema para actualización de la parametrización de un tenant por su administrador."""

    max_transaction_limit: Decimal | None = Field(default=None, gt=0)
    fee_type: FeeType | None = None
    fee_value: Decimal | None = Field(default=None, ge=0)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rates: dict[str, Any] | None = None
    webhook_url: str | None = None

    @field_validator("base_currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        currency = v.upper()
        if currency not in ("COP", "USD", "EUR"):
            raise ValueError("La divisa base debe ser COP, USD o EUR")
        return currency


class TenantResponseDTO(BaseModel):
    """Esquema de respuesta para la entidad Tenant."""

    id: uuid.UUID
    name: str
    slug: str
    max_transaction_limit: Decimal
    fee_type: FeeType
    fee_value: Decimal
    base_currency: str
    exchange_rates: dict[str, Any]
    webhook_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

