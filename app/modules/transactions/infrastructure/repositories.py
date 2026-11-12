from datetime import datetime
from decimal import Decimal
import uuid
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.transactions.domain.entities import OperationType, Transaction, TransactionStatus
from app.modules.transactions.domain.repositories import TransactionRepository
from app.modules.transactions.infrastructure.models import TransactionModel


class PostgresTransactionRepository(TransactionRepository):
    """Implementación de persistencia para el historial de transacciones financieras."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: TransactionModel) -> Transaction:
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
            idempotency_key=model.idempotency_key,
            correlation_id=model.correlation_id,
            created_at=model.created_at,
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
            idempotency_key=transaction.idempotency_key,
            correlation_id=transaction.correlation_id,
            created_at=transaction.created_at,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_entity(model)

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
        tenant_id: uuid.UUID,
    ) -> Transaction | None:
        stmt = select(TransactionModel).where(
            TransactionModel.idempotency_key == idempotency_key,
            TransactionModel.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

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
            .order_by(TransactionModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

    async def list_by_owner_accounts(
        self,
        tenant_id: uuid.UUID,
        account_numbers: list[str],
        skip: int = 0,
        limit: int = 50,
        operation_type: OperationType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[Transaction], int]:
        if not account_numbers:
            return [], 0

        base_filter = [
            TransactionModel.tenant_id == tenant_id,
            or_(
                TransactionModel.source_account_number.in_(account_numbers),
                TransactionModel.destination_account_number.in_(account_numbers),
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
            .order_by(TransactionModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

