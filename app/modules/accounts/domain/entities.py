from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import uuid
import secrets

from app.core.exceptions import BankOSException, InsufficientFundsException


class AccountStatus(str, Enum):
    """Estados del ciclo de vida de una cuenta bancaria en BankOS."""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    BLOCKED = "BLOCKED"


@dataclass
class Account:
    """Entidad de dominio que representa una cuenta bancaria con aislamiento multi-tenant."""

    id: uuid.UUID
    account_number: str
    owner_id: uuid.UUID
    tenant_id: uuid.UUID
    balance: Decimal
    currency: str
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reactivated_at: datetime | None = None
    can_deactivate_at: datetime | None = None
    requires_admin_reactivation: bool = False

    @classmethod
    def create(
        cls,
        owner_id: uuid.UUID,
        tenant_id: uuid.UUID,
        currency: str = "COP",
        initial_balance: Decimal = Decimal("0.0000"),
        account_number: str | None = None,
    ) -> "Account":
        now = datetime.now(timezone.utc)
        generated_number = account_number or cls.generate_account_number()
        return cls(
            id=uuid.uuid4(),
            account_number=generated_number,
            owner_id=owner_id,
            tenant_id=tenant_id,
            balance=Decimal(str(initial_balance)),
            currency=currency.upper(),
            status=AccountStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def generate_account_number() -> str:
        """Genera un número de cuenta único y legible de 10 dígitos."""
        # Generar número de 10 dígitos iniciando con dígito distinto de 0
        random_digits = "".join(str(secrets.randbelow(10)) for _ in range(9))
        first_digit = str(secrets.randbelow(9) + 1)
        return f"{first_digit}{random_digits}"

    def ensure_can_operate(self) -> None:
        """Regla de dominio: verifica si la cuenta se encuentra activa para realizar transacciones."""
        if self.status == AccountStatus.INACTIVE:
            raise BankOSException(
                message=f"La cuenta {self.account_number} se encuentra inactiva. No se admiten operaciones financieras.",
                internal_code="ACCOUNT_INACTIVE",
                status_code=400,
            )
        self._ensure_not_blocked()

    def _ensure_not_blocked(self) -> None:
        if self.status == AccountStatus.BLOCKED:
            raise BankOSException(
                message=f"La cuenta {self.account_number} se encuentra bloqueada por seguridad o auditoría.",
                internal_code="ACCOUNT_BLOCKED",
                status_code=403,
            )

    def deactivate(self, *, now: datetime, administrative: bool) -> bool:
        """Aplica borrado lógico cambiando el estado a INACTIVE."""
        self._ensure_not_blocked()
        if self.status == AccountStatus.INACTIVE:
            return False
        if not administrative and self.can_deactivate_at is not None and now < self.can_deactivate_at:
            raise BankOSException(
                message="La cuenta todavía está en el plazo de espera después de su reactivación",
                internal_code="ACCOUNT_DEACTIVATION_COOLDOWN",
                status_code=409,
                data={"can_deactivate_at": self.can_deactivate_at.isoformat()},
            )
        self.status = AccountStatus.INACTIVE
        self.requires_admin_reactivation = administrative
        self.updated_at = now
        return True

    def block(self) -> None:
        """Bloquea preventivamente la cuenta bancaria."""
        self.status = AccountStatus.BLOCKED
        self.updated_at = datetime.now(timezone.utc)

    def activate(self, *, now: datetime, cooldown: timedelta, administrative: bool) -> bool:
        """Reactiva una cuenta bancaria."""
        self._ensure_not_blocked()
        if self.status == AccountStatus.ACTIVE:
            return False
        if self.requires_admin_reactivation and not administrative:
            raise BankOSException(
                message="La reactivación de esta cuenta requiere un administrador del banco",
                internal_code="ACCOUNT_REACTIVATION_REQUIRES_ADMIN",
                status_code=403,
            )
        self.status = AccountStatus.ACTIVE
        self.reactivated_at = now
        self.can_deactivate_at = now + cooldown
        self.requires_admin_reactivation = False
        self.updated_at = now
        return True

    def credit(self, amount: Decimal) -> None:
        """Abona fondos a la cuenta validando integridad."""
        self.ensure_can_operate()
        amount_dec = Decimal(str(amount))
        if amount_dec <= Decimal("0.0000"):
            raise BankOSException(
                message="El monto a abonar debe ser estrictamente positivo",
                internal_code="INVALID_AMOUNT",
                status_code=422,
            )
        self.balance += amount_dec
        self.updated_at = datetime.now(timezone.utc)

    def debit(self, amount: Decimal) -> None:
        """Debita fondos de la cuenta validando saldo suficiente."""
        self.ensure_can_operate()
        amount_dec = Decimal(str(amount))
        if amount_dec <= Decimal("0.0000"):
            raise BankOSException(
                message="El monto a debitar debe ser estrictamente positivo",
                internal_code="INVALID_AMOUNT",
                status_code=422,
            )
        if self.balance < amount_dec:
            raise InsufficientFundsException(
                message=f"Saldo insuficiente en la cuenta {self.account_number}. Saldo disponible: {self.balance}"
            )
        self.balance -= amount_dec
        self.updated_at = datetime.now(timezone.utc)

