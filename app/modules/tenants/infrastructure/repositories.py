from decimal import Decimal
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenants.domain.entities import FeeType, Tenant
from app.modules.tenants.domain.repositories import TenantRepository
from app.modules.tenants.infrastructure.models import TenantModel


class PostgresTenantRepository(TenantRepository):
    """Implementación de persistencia para Tenants usando SQLAlchemy asíncrono y PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: TenantModel) -> Tenant:
        return Tenant(
            id=model.id,
            name=model.name,
            slug=model.slug,
            max_transaction_limit=Decimal(str(model.max_transaction_limit)),
            fee_type=FeeType(model.fee_type),
            fee_value=Decimal(str(model.fee_value)),
            base_currency=model.base_currency,
            exchange_rates=model.exchange_rates or {},
            webhook_url=model.webhook_url,
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(self, tenant: Tenant) -> Tenant:
        model = TenantModel(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            max_transaction_limit=tenant.max_transaction_limit,
            fee_type=tenant.fee_type.value,
            fee_value=tenant.fee_value,
            base_currency=tenant.base_currency,
            exchange_rates=tenant.exchange_rates,
            webhook_url=tenant.webhook_url,
            is_active=tenant.is_active,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_entity(model)

    async def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        stmt = select(TenantModel).where(TenantModel.id == tenant_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = select(TenantModel).where(TenantModel.slug == slug.strip().lower())
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def update(self, tenant: Tenant) -> Tenant:
        stmt = select(TenantModel).where(TenantModel.id == tenant.id)
        result = await self.session.execute(stmt)
        model = result.scalar_one()

        model.max_transaction_limit = tenant.max_transaction_limit
        model.fee_type = tenant.fee_type.value
        model.fee_value = tenant.fee_value
        model.base_currency = tenant.base_currency
        model.exchange_rates = tenant.exchange_rates
        model.webhook_url = tenant.webhook_url
        model.is_active = tenant.is_active
        model.updated_at = tenant.updated_at

        await self.session.flush()
        return self._to_entity(model)

