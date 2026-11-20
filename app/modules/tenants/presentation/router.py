import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import TenantAccessDeniedException
from app.core.responses import APIResponse, success_response
from app.modules.audit.infrastructure.repositories import PostgresAuditRepository
from app.modules.auth_users.presentation.dependencies import (
    AuthenticatedUser,
    require_admin_role,
)
from app.modules.tenants.application.dtos import (
    CreateTenantDTO,
    PublicBankDTO,
    UpdateTenantConfigDTO,
)
from app.modules.tenants.application.use_cases import (
    GetTenantUseCase,
    RegisterTenantUseCase,
    UpdateTenantConfigUseCase,
)
from app.modules.tenants.infrastructure.repositories import PostgresTenantRepository

router = APIRouter(prefix="/tenants", tags=["Tenants"])
directory_router = APIRouter(prefix="/banks", tags=["Directorio de bancos"])


@directory_router.get(
    "",
    summary="Listar bancos habilitados para el selector público",
    response_model=APIResponse[list[PublicBankDTO]],
)
async def list_public_banks(session: AsyncSession = Depends(get_db)):
    banks = await PostgresTenantRepository(session).list_public_banks()
    return success_response(
        data=[bank.model_dump(mode="json") for bank in banks],
        message="Directorio de bancos obtenido",
        internal_code="BANKS_FETCHED",
    )


@router.post(
    "",
    summary="Registrar una nueva institución financiera (Tenant)",
    status_code=status.HTTP_201_CREATED,
)
async def register_tenant(
    dto: CreateTenantDTO,
    session: AsyncSession = Depends(get_db),
):
    """Crea un nuevo tenant con su configuración financiera inicial y tabla de divisas."""
    tenant_repo = PostgresTenantRepository(session)
    audit_repo = PostgresAuditRepository(session)
    use_case = RegisterTenantUseCase(tenant_repo, audit_repo)

    result = await use_case.execute(dto)
    await session.commit()
    return success_response(
        data=result.model_dump(mode="json"),
        message="Institución financiera registrada exitosamente",
        internal_code="TENANT_CREATED",
        status_code=status.HTTP_201_CREATED,
    )


@router.patch(
    "/config",
    summary="Actualizar parametrización del tenant autenticado",
)
async def update_tenant_config(
    dto: UpdateTenantConfigDTO,
    admin_user: AuthenticatedUser = Depends(require_admin_role),
    session: AsyncSession = Depends(get_db),
):
    """Permite al administrador modificar los límites, comisiones y tasas de su institución."""
    tenant_repo = PostgresTenantRepository(session)
    audit_repo = PostgresAuditRepository(session)
    use_case = UpdateTenantConfigUseCase(tenant_repo, audit_repo)

    result = await use_case.execute(
        tenant_id=admin_user.tenant_id,
        dto=dto,
        actor_user_id=admin_user.user_id,
    )
    await session.commit()
    return success_response(
        data=result.model_dump(mode="json"),
        message="Configuración de la institución actualizada exitosamente",
        internal_code="TENANT_CONFIG_UPDATED",
    )


@router.get(
    "/me",
    summary="Obtener información del tenant autenticado",
)
async def get_current_tenant_info(
    admin_user: AuthenticatedUser = Depends(require_admin_role),
    session: AsyncSession = Depends(get_db),
):
    """Consulta la parametrización actual de la institución del administrador autenticado."""
    tenant_repo = PostgresTenantRepository(session)
    use_case = GetTenantUseCase(tenant_repo)

    result = await use_case.execute(tenant_id=admin_user.tenant_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Información del tenant obtenida exitosamente",
        internal_code="TENANT_FETCHED",
    )


@router.patch(
    "/{tenant_id}/config",
    summary="Actualizar parametrización por ID de tenant (Verificación estricta)",
)
async def update_tenant_config_by_id(
    tenant_id: uuid.UUID,
    dto: UpdateTenantConfigDTO,
    admin_user: AuthenticatedUser = Depends(require_admin_role),
    session: AsyncSession = Depends(get_db),
):
    """Garantiza aislamiento estricto verificando que el tenant_id coincida con el token."""
    if admin_user.tenant_id != tenant_id:
        raise TenantAccessDeniedException("No tiene permisos para modificar la configuración de otra institución")

    tenant_repo = PostgresTenantRepository(session)
    audit_repo = PostgresAuditRepository(session)
    use_case = UpdateTenantConfigUseCase(tenant_repo, audit_repo)

    result = await use_case.execute(
        tenant_id=admin_user.tenant_id,
        dto=dto,
        actor_user_id=admin_user.user_id,
    )
    await session.commit()
    return success_response(
        data=result.model_dump(mode="json"),
        message="Configuración de la institución actualizada exitosamente",
        internal_code="TENANT_CONFIG_UPDATED",
    )

