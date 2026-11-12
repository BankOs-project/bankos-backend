from abc import ABC, abstractmethod
import uuid
from app.modules.audit.domain.entities import AuditLog


class AuditRepository(ABC):
    """Contrato abstracto del repositorio de auditoría (solo operaciones de inserción y lectura)."""

    @abstractmethod
    async def record(self, audit_log: AuditLog) -> AuditLog:
        """Persiste un registro de auditoría de forma inmutable."""
        pass

    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        """Obtiene la lista paginada de registros de auditoría de un tenant y el total de registros."""
        pass

