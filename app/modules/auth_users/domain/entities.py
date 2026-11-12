from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


class UserRole(str, Enum):
    """Roles operativos de usuario en BankOS."""
    ADMIN = "administrador"
    CLIENT = "cliente"


@dataclass
class User:
    """Entidad de dominio que representa a un usuario asociado estrictamente a un tenant."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    hashed_password: str
    full_name: str
    role: UserRole
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        tenant_id: uuid.UUID,
        email: str,
        hashed_password: str,
        full_name: str,
        role: UserRole,
    ) -> "User":
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            email=email.strip().lower(),
            hashed_password=hashed_password,
            full_name=full_name.strip(),
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    def deactivate(self) -> None:
        self.is_active = False
        self.updated_at = datetime.now(timezone.utc)

    def activate(self) -> None:
        self.is_active = True
        self.updated_at = datetime.now(timezone.utc)

