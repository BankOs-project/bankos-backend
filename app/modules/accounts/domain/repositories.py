from abc import ABC, abstractmethod
import uuid
from app.modules.accounts.domain.entities import Account


class AccountRepository(ABC):
    """Contrato abstracto del repositorio de cuentas con aislamiento estricto por tenant_id."""

    @abstractmethod
    async def create(self, account: Account) -> Account:
        """Persiste una nueva cuenta bancaria."""
        pass

    @abstractmethod
    async def get_by_id(self, account_id: uuid.UUID, tenant_id: uuid.UUID) -> Account | None:
        """Busca una cuenta por su identificador UUID dentro del tenant indicado."""
        pass

    @abstractmethod
    async def get_by_account_number(self, account_number: str, tenant_id: uuid.UUID) -> Account | None:
        """Busca una cuenta por su número de cuenta dentro del tenant indicado."""
        pass

    @abstractmethod
    async def get_for_update(self, account_number: str, tenant_id: uuid.UUID) -> Account | None:
        """Obtiene una cuenta aplicando bloqueo pesimista en base de datos (SELECT ... FOR UPDATE)."""
        pass

    @abstractmethod
    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[Account], int]:
        """Obtiene las cuentas asociadas a un titular específico dentro del tenant."""
        pass

    @abstractmethod
    async def list_all_in_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[Account], int]:
        """Lista todas las cuentas activas o históricas pertenecientes a la institución bancaria."""
        pass

    @abstractmethod
    async def update(self, account: Account) -> Account:
        """Actualiza el estado, saldo u otros datos de la cuenta bancaria."""
        pass

