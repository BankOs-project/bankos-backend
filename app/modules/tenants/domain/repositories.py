from abc import ABC, abstractmethod
import uuid
from app.modules.tenants.domain.entities import Tenant


class TenantRepository(ABC):
    """Contrato del repositorio para la gestión y persistencia de tenants."""

    @abstractmethod
    async def create(self, tenant: Tenant) -> Tenant:
        """Crea y persiste un nuevo tenant."""
        pass

    @abstractmethod
    async def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        """Busca un tenant por su identificador UUID."""
        pass

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Busca un tenant por su slug único."""
        pass

    @abstractmethod
    async def update(self, tenant: Tenant) -> Tenant:
        """Actualiza la información y configuración de un tenant existente."""
        pass

