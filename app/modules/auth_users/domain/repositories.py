from abc import ABC, abstractmethod
import uuid
from app.modules.auth_users.domain.entities import User


class UserRepository(ABC):
    """Contrato del repositorio para la persistencia y consulta de usuarios."""

    @abstractmethod
    async def create(self, user: User) -> User:
        """Crea y persiste un nuevo usuario."""
        pass

    @abstractmethod
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Busca un usuario por su ID primario."""
        pass

    @abstractmethod
    async def get_by_email_and_tenant(self, email: str, tenant_id: uuid.UUID) -> User | None:
        """Busca un usuario por su correo electrónico dentro del alcance de un tenant."""
        pass

    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        """Lista usuarios pertenecientes a un tenant con paginación."""
        pass

