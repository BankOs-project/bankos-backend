from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.responses import success_response
from app.modules.audit.application.use_cases import GetTenantAuditLogsUseCase
from app.modules.audit.infrastructure.repositories import PostgresAuditRepository
from app.modules.auth_users.presentation.dependencies import (
    AuthenticatedUser,
    require_admin_role,
)

router = APIRouter(prefix="/audit-logs", tags=["Auditoría"])


@router.get(
    "",
    summary="Consultar registros de auditoría del tenant (Solo Administrador)",
)
async def list_audit_logs(
    skip: int = Query(0, ge=0, description="Número de registros a omitir para paginación"),
    limit: int = Query(50, ge=1, le=100, description="Cantidad máxima de registros por página"),
    admin_user: AuthenticatedUser = Depends(require_admin_role),
    session: AsyncSession = Depends(get_db),
):
    """Consulta la bitácora inmutable de eventos administrativos y cambios de configuración del tenant."""
    audit_repo = PostgresAuditRepository(session)
    use_case = GetTenantAuditLogsUseCase(audit_repo)

    result = await use_case.execute(
        tenant_id=admin_user.tenant_id,
        skip=skip,
        limit=limit,
    )

    return success_response(
        data=result.model_dump(mode="json"),
        message="Registros de auditoría obtenidos exitosamente",
        internal_code="AUDIT_LOGS_FETCHED",
    )

