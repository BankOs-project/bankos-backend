from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthenticationFailedException
from app.core.middlewares.rate_limit import enforce_login_account_limit
from app.core.responses import success_response
from app.modules.audit.infrastructure.repositories import PostgresAuditRepository
from app.modules.auth_users.application.dtos import (
    CreateUserDTO,
    LoginRequestDTO,
    StartRegistrationDTO,
    VerifyRegistrationDTO,
)
from app.modules.auth_users.application.registration import REGISTRATION_TTL_SECONDS, RegistrationService
from app.modules.auth_users.application.use_cases import (
    AuthenticateUserUseCase,
    CreateUserUseCase,
)
from app.modules.auth_users.infrastructure.repositories import PostgresUserRepository
from app.modules.auth_users.presentation.dependencies import (
    AuthenticatedUser,
    get_current_user,
    get_optional_registration_actor,
    require_admin_role,
)
from app.modules.tenants.infrastructure.repositories import PostgresTenantRepository

router = APIRouter(prefix="/auth", tags=["Autenticación y Usuarios"])


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def start_registration(
    dto: StartRegistrationDTO,
    actor: AuthenticatedUser | None = Depends(get_optional_registration_actor),
    session: AsyncSession = Depends(get_db),
):
    registration_id = await RegistrationService(session).start(dto, actor)
    return success_response(
        data={"registration_id": str(registration_id), "expires_in": REGISTRATION_TTL_SECONDS},
        message="Completa la verificación de correo para continuar",
        internal_code="REGISTRATION_PENDING",
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.post("/register/verify", status_code=status.HTTP_201_CREATED)
async def verify_registration(
    dto: VerifyRegistrationDTO,
    actor: AuthenticatedUser | None = Depends(get_optional_registration_actor),
    session: AsyncSession = Depends(get_db),
):
    await RegistrationService(session).verify(dto, actor)
    return success_response(
        message="Registro verificado. Ya puedes iniciar sesión en el banco elegido",
        internal_code="REGISTRATION_VERIFIED",
        status_code=status.HTTP_201_CREATED,
    )


@router.post(
    "/login",
    summary="Iniciar sesión en una institución bancaria (Tenant)",
    status_code=status.HTTP_200_OK,
)
async def login(
    dto: LoginRequestDTO,
    session: AsyncSession = Depends(get_db),
):
    """Autentica dentro del banco seleccionado y genera un JWT firmado."""
    tenant_repo = PostgresTenantRepository(session)
    user_repo = PostgresUserRepository(session)
    use_case = AuthenticateUserUseCase(tenant_repo, user_repo, enforce_login_account_limit)

    result = await use_case.execute(dto)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Inicio de sesión exitoso",
        internal_code="LOGIN_SUCCESS",
    )


@router.post(
    "/users",
    summary="Crear un usuario en el tenant actual (Solo Administrador)",
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    dto: CreateUserDTO,
    admin_user: AuthenticatedUser = Depends(require_admin_role),
    session: AsyncSession = Depends(get_db),
):
    """Permite al administrador dar de alta nuevos clientes o administradores en su institución."""
    user_repo = PostgresUserRepository(session)
    audit_repo = PostgresAuditRepository(session)
    use_case = CreateUserUseCase(user_repo, audit_repo)

    result = await use_case.execute(
        tenant_id=admin_user.tenant_id,
        dto=dto,
        actor_user_id=admin_user.user_id,
    )
    await session.commit()
    return success_response(
        data=result.model_dump(mode="json"),
        message="Usuario creado exitosamente dentro del tenant",
        internal_code="USER_CREATED",
        status_code=status.HTTP_201_CREATED,
    )


@router.get(
    "/me",
    summary="Obtener perfil del usuario actualmente autenticado",
)
async def get_my_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Retorna los datos de sesión y tenant del usuario en curso."""
    user_repo = PostgresUserRepository(session)
    user = await user_repo.get_by_id(current_user.user_id)
    if not user or user.tenant_id != current_user.tenant_id or not user.is_active:
        raise AuthenticationFailedException("La sesión no corresponde a un usuario activo")

    return success_response(
        data={
            "id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
        },
        message="Perfil obtenido exitosamente",
        internal_code="USER_PROFILE",
    )

