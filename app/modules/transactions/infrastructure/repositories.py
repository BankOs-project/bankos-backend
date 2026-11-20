from datetime import datetime
from decimal import Decimal
import re
import uuid
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BankOSException, IdempotencyKeyReusedException
from app.modules.accounts.infrastructure.models import AccountModel
from app.modules.transactions.domain.entities import OperationType, Transaction, TransactionStatus
from app.modules.transactions.domain.repositories import TransactionRepository
from app.modules.transactions.infrastructure.models import TransactionModel


_STORED_KEY = re.compile(r"(?:actor|request):[0-9a-f]{32}:([0-9a-f-]{36})")


def _scoped_key(user_id: uuid.UUID, key: str) -> str:
    return f"actor:{user_id.hex}:{key}"


class PostgresTransactionRepository(TransactionRepository):
    """Implementación de persistencia para el historial de transacciones financieras."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: TransactionModel) -> Transaction:
        scoped_key = _STORED_KEY.fullmatch(model.idempotency_key)
        return Transaction(
            id=model.id,
            tenant_id=model.tenant_id,
            operation_type=OperationType(model.operation_type),
            source_account_number=model.source_account_number,
            destination_account_number=model.destination_account_number,
            original_amount=Decimal(str(model.original_amount)),
            original_currency=model.original_currency,
            converted_amount=Decimal(str(model.converted_amount)),
            destination_currency=model.destination_currency,
            exchange_rate_applied=Decimal(str(model.exchange_rate_applied)),
            fee_charged=Decimal(str(model.fee_charged)),
            status=TransactionStatus(model.status),
            idempotency_key=scoped_key[1] if scoped_key else model.idempotency_key,
            correlation_id=model.correlation_id,
            created_at=model.created_at,
            actor_user_id=model.actor_user_id,
            failure_code=model.failure_code,
        )

    async def create(self, transaction: Transaction) -> Transaction:
        model = TransactionModel(
            id=transaction.id,
            tenant_id=transaction.tenant_id,
            operation_type=transaction.operation_type.value,
            source_account_number=transaction.source_account_number,
            destination_account_number=transaction.destination_account_number,
            original_amount=transaction.original_amount,
            original_currency=transaction.original_currency,
            converted_amount=transaction.converted_amount,
            destination_currency=transaction.destination_currency,
            exchange_rate_applied=transaction.exchange_rate_applied,
            fee_charged=transaction.fee_charged,
            status=transaction.status.value,
            # El comprobante controla la ventana de la clave. El historial
            # conserva el UUID del cliente sin imponer unicidad más allá de ella.
            idempotency_key=f"request:{transaction.id.hex}:{transaction.idempotency_key}",
            correlation_id=transaction.correlation_id,
            created_at=transaction.created_at,
            actor_user_id=transaction.actor_user_id,
            failure_code=transaction.failure_code,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def get_legacy_by_idempotency_key(
        self,
        idempotency_key: str,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        operation_type: OperationType,
        since: datetime,
    ) -> Transaction | None:
        scoped_key = _scoped_key(actor_user_id, idempotency_key)
        stmt = select(TransactionModel).where(
            TransactionModel.idempotency_key.in_((scoped_key, idempotency_key, idempotency_key.upper())),
            TransactionModel.tenant_id == tenant_id,
            TransactionModel.status == TransactionStatus.SUCCESS.value,
            TransactionModel.created_at > since,
        )
        result = await self.session.execute(stmt)
        models = {model.idempotency_key: model for model in result.scalars()}
        if model := models.get(scoped_key):
            if model.operation_type != operation_type.value:
                raise IdempotencyKeyReusedException()
            return self._to_entity(model)
        if models:
            # Los registros anteriores no guardaban el actor. Evitar repetir el
            # movimiento o compartir su respuesta con el usuario equivocado.
            raise BankOSException(
                message="La operación se registró con una clave anterior. Consulta el historial antes de reintentar.",
                internal_code="LEGACY_IDEMPOTENCY_KEY",
                status_code=409,
            )
        return None

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
        base_filter = [TransactionModel.tenant_id == tenant_id]

        if account_number:
            base_filter.append(
                or_(
                    TransactionModel.source_account_number == account_number,
                    TransactionModel.destination_account_number == account_number,
                )
            )
        if operation_type:
            base_filter.append(TransactionModel.operation_type == operation_type.value)
        if start_date:
            base_filter.append(TransactionModel.created_at >= start_date)
        if end_date:
            base_filter.append(TransactionModel.created_at <= end_date)

        count_stmt = select(func.count()).select_from(TransactionModel).where(*base_filter)
        count_res = await self.session.execute(count_stmt)
        total = count_res.scalar_one() or 0

        stmt = (
            select(TransactionModel)
            .where(*base_filter)
            .order_by(TransactionModel.created_at.desc(), TransactionModel.id.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

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
        account_numbers = select(AccountModel.account_number).where(
            AccountModel.tenant_id == tenant_id,
            AccountModel.owner_id == owner_id,
        )
        if account_number:
            account_numbers = account_numbers.where(AccountModel.account_number == account_number)

        matches_account = or_(
            TransactionModel.source_account_number.in_(account_numbers),
            TransactionModel.destination_account_number.in_(account_numbers),
        )
        own_rejection = and_(
            TransactionModel.status == TransactionStatus.ERROR.value,
            TransactionModel.actor_user_id == owner_id,
        )
        if account_number:
            own_rejection = and_(own_rejection, matches_account)

        base_filter = [
            TransactionModel.tenant_id == tenant_id,
            or_(
                and_(TransactionModel.status == TransactionStatus.SUCCESS.value, matches_account),
                own_rejection,
            ),
        ]

        if operation_type:
            base_filter.append(TransactionModel.operation_type == operation_type.value)
        if start_date:
            base_filter.append(TransactionModel.created_at >= start_date)
        if end_date:
            base_filter.append(TransactionModel.created_at <= end_date)

        count_stmt = select(func.count()).select_from(TransactionModel).where(*base_filter)
        count_res = await self.session.execute(count_stmt)
        total = count_res.scalar_one() or 0

        stmt = (
            select(TransactionModel)
            .where(*base_filter)
            .order_by(TransactionModel.created_at.desc(), TransactionModel.id.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

