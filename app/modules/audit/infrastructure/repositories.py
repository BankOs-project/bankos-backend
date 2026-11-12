import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.domain.entities import AuditLog
from app.modules.audit.domain.repositories import AuditRepository
from app.modules.audit.infrastructure.models import AuditLogModel


class PostgresAuditRepository(AuditRepository):
    """Implementación de persistencia para auditoría utilizando SQLAlchemy asíncrono y PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: AuditLogModel) -> AuditLog:
        return AuditLog(
            id=model.id,
            tenant_id=model.tenant_id,
            user_id=model.user_id,
            action=model.action,
            previous_state=model.previous_state,
            new_state=model.new_state,
            created_at=model.created_at,
        )

    async def record(self, audit_log: AuditLog) -> AuditLog:
        model = AuditLogModel(
            id=audit_log.id,
            tenant_id=audit_log.tenant_id,
            user_id=audit_log.user_id,
            action=audit_log.action,
            previous_state=audit_log.previous_state,
            new_state=audit_log.new_state,
            created_at=audit_log.created_at,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_entity(model)

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        count_stmt = select(func.count()).select_from(AuditLogModel).where(AuditLogModel.tenant_id == tenant_id)
        total_result = await self.session.execute(count_stmt)
        total = total_result.scalar_one() or 0

        stmt = (
            select(AuditLogModel)
            .where(AuditLogModel.tenant_id == tenant_id)
            .order_by(AuditLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

