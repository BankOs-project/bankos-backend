from abc import ABC, abstractmethod
from datetime import datetime
import uuid
from app.modules.transactions.domain.entities import OperationType, Transaction


class TransactionRepository(ABC):
    """Contrato del repositorio para la persistencia y consulta del historial de transacciones."""

    @abstractmethod
    async def create(self, transaction: Transaction) -> Transaction:
        """Persiste un nuevo registro de transacción de forma inmutable."""
        pass

    @abstractmethod
    async def get_legacy_by_idempotency_key(
        self,
        idempotency_key: str,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        operation_type: OperationType,
        since: datetime,
    ) -> Transaction | None:
        """Busca un movimiento previo a la introducción de comprobantes HTTP."""
        pass

    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        account_number: str | None = None,
        operation_type: OperationType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[Transaction], int]:
        """Lista el historial de transacciones del tenant con filtros opcionales y paginación."""
        pass

    @abstractmethod
    async def list_by_owner_accounts(
        self,
        tenant_id: uuid.UUID,
        owner_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        account_number: str | None = None,
        operation_type: OperationType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[Transaction], int]:
        """Lista transacciones filtradas por las cuentas de un titular específico."""
        pass


class WebhookNotifierPort(ABC):
    """Puerto abstracto para el despacho asíncrono de notificaciones webhook."""

    @abstractmethod
    async def dispatch(
        self,
        webhook_url: str,
        event_type: str,
        payload: dict,
        correlation_id: str,
    ) -> None:
        """Envía una notificación HTTP POST al webhook configurado por el tenant."""
        pass

