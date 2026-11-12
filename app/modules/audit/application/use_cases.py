from typing import Any
import uuid
from app.modules.audit.application.dtos import AuditLogResponseDTO, PaginatedAuditLogsResponseDTO
from app.modules.audit.domain.entities import AuditLog
from app.modules.audit.domain.repositories import AuditRepository


class RecordAuditActionUseCase:
    """Caso de uso para registrar una acción administrativa o financiera inmutable."""

    def __init__(self, audit_repository: AuditRepository) -> None:
        self.audit_repository = audit_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        action: str,
        user_id: uuid.UUID | None = None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
    ) -> AuditLogResponseDTO:
        audit_log = AuditLog.create(
            tenant_id=tenant_id,
            action=action,
            user_id=user_id,
            previous_state=previous_state,
            new_state=new_state,
        )
        saved = await self.audit_repository.record(audit_log)
        return AuditLogResponseDTO(
            id=saved.id,
            tenant_id=saved.tenant_id,
            user_id=saved.user_id,
            action=saved.action,
            previous_state=saved.previous_state,
            new_state=saved.new_state,
            created_at=saved.created_at,
        )


class GetTenantAuditLogsUseCase:
    """Caso de uso para listar los registros de auditoría de un tenant específico."""

    def __init__(self, audit_repository: AuditRepository) -> None:
        self.audit_repository = audit_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> PaginatedAuditLogsResponseDTO:
        logs, total = await self.audit_repository.list_by_tenant(
            tenant_id=tenant_id,
            skip=skip,
            limit=limit,
        )
        items = [
            AuditLogResponseDTO(
                id=log.id,
                tenant_id=log.tenant_id,
                user_id=log.user_id,
                action=log.action,
                previous_state=log.previous_state,
                new_state=log.new_state,
                created_at=log.created_at,
            )
            for log in logs
        ]
        return PaginatedAuditLogsResponseDTO(
            total=total,
            skip=skip,
            limit=limit,
            items=items,
        )

