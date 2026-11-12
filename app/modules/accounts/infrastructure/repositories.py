from decimal import Decimal
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.domain.entities import Account, AccountStatus
from app.modules.accounts.domain.repositories import AccountRepository
from app.modules.accounts.infrastructure.models import AccountModel


class PostgresAccountRepository(AccountRepository):
    """Implementación de persistencia para cuentas bancarias con aislamiento multi-tenant estricto."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: AccountModel) -> Account:
        return Account(
            id=model.id,
            account_number=model.account_number,
            owner_id=model.owner_id,
            tenant_id=model.tenant_id,
            balance=Decimal(str(model.balance)),
            currency=model.currency,
            status=AccountStatus(model.status),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(self, account: Account) -> Account:
        model = AccountModel(
            id=account.id,
            account_number=account.account_number,
            owner_id=account.owner_id,
            tenant_id=account.tenant_id,
            balance=account.balance,
            currency=account.currency,
            status=account.status.value,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_entity(model)

    async def get_by_id(self, account_id: uuid.UUID, tenant_id: uuid.UUID) -> Account | None:
        stmt = select(AccountModel).where(
            AccountModel.id == account_id,
            AccountModel.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_account_number(self, account_number: str, tenant_id: uuid.UUID) -> Account | None:
        stmt = select(AccountModel).where(
            AccountModel.account_number == account_number,
            AccountModel.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_for_update(self, account_number: str, tenant_id: uuid.UUID) -> Account | None:
        """Obtiene una cuenta con bloqueo pesimista en PostgreSQL (SELECT ... FOR UPDATE)."""
        stmt = (
            select(AccountModel)
            .where(
                AccountModel.account_number == account_number,
                AccountModel.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[Account], int]:
        count_stmt = select(func.count()).select_from(AccountModel).where(
            AccountModel.owner_id == owner_id,
            AccountModel.tenant_id == tenant_id,
        )
        count_res = await self.session.execute(count_stmt)
        total = count_res.scalar_one() or 0

        stmt = (
            select(AccountModel)
            .where(
                AccountModel.owner_id == owner_id,
                AccountModel.tenant_id == tenant_id,
            )
            .order_by(AccountModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

    async def list_all_in_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[Account], int]:
        count_stmt = select(func.count()).select_from(AccountModel).where(
            AccountModel.tenant_id == tenant_id,
        )
        count_res = await self.session.execute(count_stmt)
        total = count_res.scalar_one() or 0

        stmt = (
            select(AccountModel)
            .where(AccountModel.tenant_id == tenant_id)
            .order_by(AccountModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

    async def update(self, account: Account) -> Account:
        stmt = select(AccountModel).where(
            AccountModel.id == account.id,
            AccountModel.tenant_id == account.tenant_id,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one()

        model.balance = account.balance
        model.status = account.status.value
        model.currency = account.currency
        model.updated_at = account.updated_at

        await self.session.flush()
        return self._to_entity(model)

