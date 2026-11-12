from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass
class AuditLog:
    """Entidad de dominio que representa un registro inmutable de auditoría."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    previous_state: dict[str, Any] | None
    new_state: dict[str, Any] | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        tenant_id: uuid.UUID,
        action: str,
        user_id: uuid.UUID | None = None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
    ) -> "AuditLog":
        return cls(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            previous_state=previous_state,
            new_state=new_state,
            created_at=datetime.now(timezone.utc),
        )

