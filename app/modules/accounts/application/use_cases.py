import uuid
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

        return self._to_dto(saved)

    def _to_dto(self, account: Account) -> AccountResponseDTO:
        return AccountResponseDTO(
            id=account.id,
            account_number=account.account_number,
            owner_id=account.owner_id,
            tenant_id=account.tenant_id,
            balance=account.balance,
            currency=account.currency,
            status=account.status,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


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

        items = [
            AccountResponseDTO(
                id=a.id,
                account_number=a.account_number,
                owner_id=a.owner_id,
                tenant_id=a.tenant_id,
                balance=a.balance,
                currency=a.currency,
                status=a.status,
                created_at=a.created_at,
                updated_at=a.updated_at,
            )
            for a in accounts
        ]

        return PaginatedAccountsResponseDTO(
            total=total,
            skip=skip,
            limit=limit,
            items=items,
        )


class DeactivateAccountUseCase:
    """Caso de uso para realizar borrado lógico (Soft Delete) y registrar en auditoría inmutable."""

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
    ) -> AccountResponseDTO:
        account = await self.account_repository.get_by_account_number(
            account_number=account_number,
            tenant_id=tenant_id,
        )
        if not account:
            raise ResourceNotFoundException(
                f"La cuenta bancaria '{account_number}' no fue encontrada en esta institución"
            )

        # Regla de autorización: si es cliente, debe ser el titular de la cuenta
        if actor_role == UserRole.CLIENT and account.owner_id != actor_user_id:
            raise AuthorizationFailedException(
                "No posee autorización para desactivar una cuenta de la cual no es titular"
            )

        previous_state = {
            "account_number": account.account_number,
            "status": account.status.value,
            "balance": str(account.balance),
        }

        # Aplicar borrado lógico
        account.deactivate()
        updated = await self.account_repository.update(account)

        new_state = {
            "account_number": updated.account_number,
            "status": updated.status.value,
            "balance": str(updated.balance),
        }

        # Registro obligatorio en auditoría inmutable
        audit = AuditLog.create(
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action="ACCOUNT_DEACTIVATED",
            previous_state=previous_state,
            new_state=new_state,
        )
        await self.audit_repository.record(audit)

        return AccountResponseDTO(
            id=updated.id,
            account_number=updated.account_number,
            owner_id=updated.owner_id,
            tenant_id=updated.tenant_id,
            balance=updated.balance,
            currency=updated.currency,
            status=updated.status,
            created_at=updated.created_at,
            updated_at=updated.updated_at,
        )

