from datetime import datetime, timedelta, timezone
import uuid

from app.core.config import settings
from app.core.exceptions import (
    AuthorizationFailedException,
    BankOSException,
    ResourceNotFoundException,
)
from app.modules.accounts.application.dtos import (
    AccountResponseDTO,
    CreateAccountDTO,
    PaginatedAccountsResponseDTO,
)
from app.modules.accounts.domain.entities import Account
from app.modules.accounts.domain.repositories import AccountRepository
from app.modules.audit.domain.entities import AuditLog
from app.modules.audit.domain.repositories import AuditRepository
from app.modules.auth_users.domain.entities import UserRole
from app.modules.auth_users.domain.repositories import UserRepository


class CreateAccountUseCase:
    """Caso de uso para apertura de nuevas cuentas bancarias dentro de una institución."""

    def __init__(
        self,
        account_repository: AccountRepository,
        user_repository: UserRepository,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self.account_repository = account_repository
        self.user_repository = user_repository
        self.audit_repository = audit_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: CreateAccountDTO,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
    ) -> AccountResponseDTO:
        # 1. Determinar el titular de la cuenta según el rol
        if actor_role == UserRole.CLIENT:
            owner_id = actor_user_id
        else:
            owner_id = dto.owner_id or actor_user_id

        # 2. Validar que el titular exista y pertenezca al mismo tenant
        owner = await self.user_repository.get_by_id(owner_id)
        if not owner or owner.tenant_id != tenant_id:
            raise BankOSException(
                message="El titular especificado no pertenece a esta institución financiera o no existe",
                internal_code="INVALID_ACCOUNT_OWNER",
                status_code=400,
            )

        # 3. Crear entidad de cuenta
        account = Account.create(
            owner_id=owner_id,
            tenant_id=tenant_id,
            currency=dto.currency,
            initial_balance=dto.initial_deposit,
        )

        saved = await self.account_repository.create(account)

        # 4. Registrar auditoría inmutable
        if self.audit_repository:
            audit = AuditLog.create(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action="ACCOUNT_OPENED",
                previous_state=None,
                new_state={
                    "account_number": saved.account_number,
                    "owner_id": str(saved.owner_id),
                    "currency": saved.currency,
                    "initial_balance": str(saved.balance),
                    "status": saved.status.value,
                },
            )
            await self.audit_repository.record(audit)

        return AccountResponseDTO.model_validate(saved, from_attributes=True)


class ListAccountsUseCase:
    """Caso de uso para consultar cuentas con control estricto de visibilidad según rol."""

    def __init__(self, account_repository: AccountRepository) -> None:
        self.account_repository = account_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
        owner_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> PaginatedAccountsResponseDTO:
        if actor_role == UserRole.CLIENT:
            # Los clientes ÚNICAMENTE ven las cuentas de las cuales son titulares
            accounts, total = await self.account_repository.list_by_owner(
                owner_id=actor_user_id,
                tenant_id=tenant_id,
                skip=skip,
                limit=limit,
            )
        else:
            # Administradores pueden listar todo el tenant o filtrar por un cliente particular
            if owner_id:
                accounts, total = await self.account_repository.list_by_owner(
                    owner_id=owner_id,
                    tenant_id=tenant_id,
                    skip=skip,
                    limit=limit,
                )
            else:
                accounts, total = await self.account_repository.list_all_in_tenant(
                    tenant_id=tenant_id,
                    skip=skip,
                    limit=limit,
                )

        items = [AccountResponseDTO.model_validate(account, from_attributes=True) for account in accounts]

        return PaginatedAccountsResponseDTO(
            total=total,
            skip=skip,
            limit=limit,
            items=items,
        )


class ChangeAccountStatusUseCase:
    """Cambia el estado bajo el mismo bloqueo que usan las operaciones financieras."""

    def __init__(
        self,
        account_repository: AccountRepository,
        audit_repository: AuditRepository,
    ) -> None:
        self.account_repository = account_repository
        self.audit_repository = audit_repository

    async def execute(
        self,
        account_number: str,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
        *,
        reactivate: bool,
    ) -> AccountResponseDTO:
        account = await self.account_repository.get_for_update(
            account_number=account_number,
            tenant_id=tenant_id,
        )
        if not account:
            raise ResourceNotFoundException(
                f"La cuenta bancaria '{account_number}' no fue encontrada en esta institución"
            )

        administrative = actor_role == UserRole.ADMIN
        if not administrative and account.owner_id != actor_user_id:
            raise AuthorizationFailedException("No posee autorización para cambiar el estado de esta cuenta")

        previous = self._audit_state(account)
        # Leer el reloj del servidor después de obtener el bloqueo de la fila.
        now = datetime.now(timezone.utc)
        if reactivate:
            changed = account.activate(
                now=now,
                cooldown=timedelta(hours=settings.ACCOUNT_DEACTIVATION_COOLDOWN_HOURS),
                administrative=administrative,
            )
        else:
            changed = account.deactivate(now=now, administrative=administrative)

        if changed:
            account = await self.account_repository.update(account)
            await self.audit_repository.record(AuditLog.create(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action="ACCOUNT_REACTIVATED" if reactivate else "ACCOUNT_DEACTIVATED",
                previous_state=previous,
                new_state={**self._audit_state(account), "actor_role": actor_role.value},
            ))
        return AccountResponseDTO.model_validate(account, from_attributes=True)

    @staticmethod
    def _audit_state(account: Account) -> dict:
        return {
            "account_number": account.account_number,
            "status": account.status.value,
            "balance": str(account.balance),
            "requires_admin_reactivation": account.requires_admin_reactivation,
            "reactivated_at": account.reactivated_at.isoformat() if account.reactivated_at else None,
            "can_deactivate_at": account.can_deactivate_at.isoformat() if account.can_deactivate_at else None,
        }

