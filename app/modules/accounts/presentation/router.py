import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthorizationFailedException, ResourceNotFoundException
from app.core.responses import APIResponse, success_response
from app.modules.accounts.application.dtos import AccountResponseDTO, CreateAccountDTO
from app.modules.accounts.domain.entities import AccountStatus
from app.modules.accounts.application.use_cases import (
    ChangeAccountStatusUseCase,
    CreateAccountUseCase,
    ListAccountsUseCase,
)
from app.modules.accounts.infrastructure.repositories import PostgresAccountRepository
from app.modules.audit.infrastructure.repositories import PostgresAuditRepository
from app.modules.auth_users.domain.entities import UserRole
from app.modules.auth_users.infrastructure.repositories import PostgresUserRepository
from app.modules.auth_users.presentation.dependencies import (
    AuthenticatedUser,
    get_current_user,
)

router = APIRouter(prefix="/accounts", tags=["Cuentas Bancarias"])


@router.post(
    "",
    summary="Apertura de cuenta bancaria",
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    dto: CreateAccountDTO,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Crea una nueva cuenta bancaria en la divisa seleccionada dentro del tenant.
    
    - Si el usuario es 'cliente', la cuenta se vincula automáticamente a su ID.
    - Si el usuario es 'administrador', puede especificar el owner_id de un cliente de la institución.
    """
    account_repo = PostgresAccountRepository(session)
    user_repo = PostgresUserRepository(session)
    audit_repo = PostgresAuditRepository(session)
    use_case = CreateAccountUseCase(account_repo, user_repo, audit_repo)

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        dto=dto,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
    )
    await session.commit()

    return success_response(
        data=result.model_dump(mode="json"),
        message="Cuenta bancaria creada exitosamente",
        internal_code="ACCOUNT_CREATED",
        status_code=status.HTTP_201_CREATED,
    )


@router.get(
    "",
    summary="Listar cuentas bancarias",
)
async def list_accounts(
    owner_id: uuid.UUID | None = Query(None, description="Filtrar por titular (solo aplicable para rol administrador)"),
    skip: int = Query(0, ge=0, description="Registros a omitir"),
    limit: int = Query(50, ge=1, le=100, description="Límite por página"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Obtiene el listado de cuentas bancarias según el rol:
    
    - 'cliente': devuelve únicamente las cuentas de las cuales es titular.
    - 'administrador': devuelve todas las cuentas de la institución financiera o las del cliente filtrado.
    """
    account_repo = PostgresAccountRepository(session)
    use_case = ListAccountsUseCase(account_repo)

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
        owner_id=owner_id,
        skip=skip,
        limit=limit,
    )

    return success_response(
        data=result.model_dump(mode="json"),
        message="Cuentas bancarias obtenidas exitosamente",
        internal_code="ACCOUNTS_FETCHED",
    )


@router.get(
    "/{account_number}/destination",
    summary="Confirmar destino de una transferencia interna",
)
async def get_transfer_destination(
    account_number: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    account = await PostgresAccountRepository(session).get_by_account_number(
        account_number=account_number,
        tenant_id=current_user.tenant_id,
    )
    if not account or account.status != AccountStatus.ACTIVE:
        raise ResourceNotFoundException("Cuenta destino no disponible en esta institución")

    owner = await PostgresUserRepository(session).get_by_id(account.owner_id)
    if not owner or owner.tenant_id != current_user.tenant_id:
        raise ResourceNotFoundException("Cuenta destino no disponible en esta institución")

    return success_response(
        data={
            "account_number": account.account_number,
            "holder_name": owner.full_name,
            "currency": account.currency,
        },
        message="Cuenta destino encontrada",
        internal_code="TRANSFER_DESTINATION_FOUND",
    )


@router.get(
    "/{account_number}",
    summary="Consultar detalle de una cuenta bancaria",
    response_model=APIResponse[AccountResponseDTO],
)
async def get_account_detail(
    account_number: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Consulta los datos y saldo actual de una cuenta bancaria asegurando autorización."""
    account_repo = PostgresAccountRepository(session)
    account = await account_repo.get_by_account_number(
        account_number=account_number,
        tenant_id=current_user.tenant_id,
    )
    if not account:
        raise ResourceNotFoundException(
            f"La cuenta bancaria '{account_number}' no fue encontrada en esta institución"
        )

    # Validar que si es cliente, sea el titular de la cuenta
    if current_user.role == UserRole.CLIENT and account.owner_id != current_user.user_id:
        raise AuthorizationFailedException("No posee permisos para consultar esta cuenta")

    return success_response(
        data=AccountResponseDTO.model_validate(account, from_attributes=True).model_dump(mode="json"),
        message="Detalle de la cuenta obtenido exitosamente",
        internal_code="ACCOUNT_DETAIL_FETCHED",
    )


@router.patch(
    "/{account_number}/deactivate",
    summary="Desactivar cuenta bancaria (Soft Delete con auditoría)",
    response_model=APIResponse[AccountResponseDTO],
)
async def deactivate_account(
    account_number: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    return await _change_account_status(account_number, current_user, session, reactivate=False)


@router.patch(
    "/{account_number}/reactivate",
    summary="Reactivar cuenta inactiva",
    response_model=APIResponse[AccountResponseDTO],
)
async def reactivate_account(
    account_number: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    return await _change_account_status(account_number, current_user, session, reactivate=True)


async def _change_account_status(
    account_number: str,
    current_user: AuthenticatedUser,
    session: AsyncSession,
    *,
    reactivate: bool,
):
    use_case = ChangeAccountStatusUseCase(
        PostgresAccountRepository(session), PostgresAuditRepository(session),
    )

    result = await use_case.execute(
        account_number=account_number,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
        reactivate=reactivate,
    )
    await session.commit()

    return success_response(
        data=result.model_dump(mode="json"),
        message="Cuenta bancaria reactivada" if reactivate else "Cuenta bancaria desactivada",
        internal_code="ACCOUNT_REACTIVATED" if reactivate else "ACCOUNT_DEACTIVATED",
    )

