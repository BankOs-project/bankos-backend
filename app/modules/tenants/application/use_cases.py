import uuid
from app.core.exceptions import BankOSException, TenantNotFoundException
from app.modules.audit.domain.entities import AuditLog
from app.modules.audit.domain.repositories import AuditRepository
from app.modules.tenants.application.dtos import (
    CreateTenantDTO,
    TenantResponseDTO,
    UpdateTenantConfigDTO,
)
from app.modules.tenants.domain.entities import Tenant
from app.modules.tenants.domain.repositories import TenantRepository


class RegisterTenantUseCase:
    """Caso de uso para registrar una nueva institución financiera en la plataforma."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self.tenant_repository = tenant_repository
        self.audit_repository = audit_repository

    async def execute(self, dto: CreateTenantDTO, actor_user_id: uuid.UUID | None = None) -> TenantResponseDTO:
        existing = await self.tenant_repository.get_by_slug(dto.slug)
        if existing:
            raise BankOSException(
                message=f"El slug de tenant '{dto.slug}' ya se encuentra registrado",
                internal_code="TENANT_SLUG_ALREADY_EXISTS",
                status_code=409,
            )

        tenant = Tenant.create(
            name=dto.name,
            slug=dto.slug,
            max_transaction_limit=dto.max_transaction_limit,
            fee_type=dto.fee_type,
            fee_value=dto.fee_value,
            base_currency=dto.base_currency,
            exchange_rates=dto.exchange_rates,
            webhook_url=dto.webhook_url,
        )

        saved = await self.tenant_repository.create(tenant)

        if self.audit_repository:
            audit = AuditLog.create(
                tenant_id=saved.id,
                user_id=actor_user_id,
                action="TENANT_REGISTERED",
                previous_state=None,
                new_state={
                    "name": saved.name,
                    "slug": saved.slug,
                    "base_currency": saved.base_currency,
                    "max_transaction_limit": str(saved.max_transaction_limit),
                },
            )
            await self.audit_repository.record(audit)

        return self._to_response_dto(saved)

    def _to_response_dto(self, tenant: Tenant) -> TenantResponseDTO:
        return TenantResponseDTO(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            max_transaction_limit=tenant.max_transaction_limit,
            fee_type=tenant.fee_type,
            fee_value=tenant.fee_value,
            base_currency=tenant.base_currency,
            exchange_rates=tenant.exchange_rates,
            webhook_url=tenant.webhook_url,
            is_active=tenant.is_active,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )


class UpdateTenantConfigUseCase:
    """Caso de uso para que un administrador modifique la parametrización de su tenant."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        audit_repository: AuditRepository,
    ) -> None:
        self.tenant_repository = tenant_repository
        self.audit_repository = audit_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: UpdateTenantConfigDTO,
        actor_user_id: uuid.UUID,
    ) -> TenantResponseDTO:
        tenant = await self.tenant_repository.get_by_id(tenant_id, for_update=True)
        if not tenant:
            raise TenantNotFoundException(f"No existe el tenant con ID: {tenant_id}")

        # Captura de estado anterior para auditoría
        previous_state = {
            "max_transaction_limit": str(tenant.max_transaction_limit),
            "fee_type": tenant.fee_type.value,
            "fee_value": str(tenant.fee_value),
            "base_currency": tenant.base_currency,
            "exchange_rates": tenant.exchange_rates,
            "webhook_url": tenant.webhook_url,
        }

        # Aplicar actualización
        tenant.update_configuration(
            max_transaction_limit=dto.max_transaction_limit,
            fee_type=dto.fee_type,
            fee_value=dto.fee_value,
            base_currency=dto.base_currency,
            exchange_rates=dto.exchange_rates,
            webhook_url=dto.webhook_url,
        )

        updated = await self.tenant_repository.update(tenant)

        # Captura de nuevo estado
        new_state = {
            "max_transaction_limit": str(updated.max_transaction_limit),
            "fee_type": updated.fee_type.value,
            "fee_value": str(updated.fee_value),
            "base_currency": updated.base_currency,
            "exchange_rates": updated.exchange_rates,
            "webhook_url": updated.webhook_url,
        }

        # Registro obligatorio e inmutable de auditoría
        audit = AuditLog.create(
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action="TENANT_CONFIG_UPDATED",
            previous_state=previous_state,
            new_state=new_state,
        )
        await self.audit_repository.record(audit)

        return TenantResponseDTO(
            id=updated.id,
            name=updated.name,
            slug=updated.slug,
            max_transaction_limit=updated.max_transaction_limit,
            fee_type=updated.fee_type,
            fee_value=updated.fee_value,
            base_currency=updated.base_currency,
            exchange_rates=updated.exchange_rates,
            webhook_url=updated.webhook_url,
            is_active=updated.is_active,
            created_at=updated.created_at,
            updated_at=updated.updated_at,
        )


class GetTenantUseCase:
    """Caso de uso para consultar un tenant por su identificador único."""

    def __init__(self, tenant_repository: TenantRepository) -> None:
        self.tenant_repository = tenant_repository

    async def execute(self, tenant_id: uuid.UUID) -> TenantResponseDTO:
        tenant = await self.tenant_repository.get_by_id(tenant_id)
        if not tenant:
            raise TenantNotFoundException(f"No existe el tenant con ID: {tenant_id}")

        return TenantResponseDTO(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            max_transaction_limit=tenant.max_transaction_limit,
            fee_type=tenant.fee_type,
            fee_value=tenant.fee_value,
            base_currency=tenant.base_currency,
            exchange_rates=tenant.exchange_rates,
            webhook_url=tenant.webhook_url,
            is_active=tenant.is_active,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )

