from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
import uuid


class FeeType(str, Enum):
    """Tipo de comisión aplicada a transacciones salientes del tenant."""
    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"


class Currency(str, Enum):
    """Monedas base admitidas en BankOS."""
    COP = "COP"
    USD = "USD"
    EUR = "EUR"


@dataclass
class Tenant:
    """Entidad de dominio que representa a una institución financiera independiente."""

    id: uuid.UUID
    name: str
    slug: str
    max_transaction_limit: Decimal
    fee_type: FeeType
    fee_value: Decimal
    base_currency: str
    exchange_rates: dict[str, Any]
    webhook_url: str | None = None
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        name: str,
        slug: str,
        max_transaction_limit: Decimal,
        fee_type: FeeType,
        fee_value: Decimal,
        base_currency: str = "COP",
        exchange_rates: dict[str, Any] | None = None,
        webhook_url: str | None = None,
    ) -> "Tenant":
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid.uuid4(),
            name=name.strip(),
            slug=slug.strip().lower(),
            max_transaction_limit=Decimal(str(max_transaction_limit)),
            fee_type=fee_type,
            fee_value=Decimal(str(fee_value)),
            base_currency=base_currency.upper(),
            exchange_rates=exchange_rates or {},
            webhook_url=webhook_url.strip() if webhook_url else None,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    def update_configuration(
        self,
        max_transaction_limit: Decimal | None = None,
        fee_type: FeeType | None = None,
        fee_value: Decimal | None = None,
        base_currency: str | None = None,
        exchange_rates: dict[str, Any] | None = None,
        webhook_url: str | None = None,
    ) -> None:
        """Actualiza la parametrización financiera y de red del tenant."""
        if max_transaction_limit is not None:
            self.max_transaction_limit = Decimal(str(max_transaction_limit))
        if fee_type is not None:
            self.fee_type = fee_type
        if fee_value is not None:
            self.fee_value = Decimal(str(fee_value))
        if base_currency is not None:
            self.base_currency = base_currency.upper()
        if exchange_rates is not None:
            self.exchange_rates = exchange_rates
        if webhook_url is not None:
            self.webhook_url = webhook_url.strip() if webhook_url else None
        self.updated_at = datetime.now(timezone.utc)

