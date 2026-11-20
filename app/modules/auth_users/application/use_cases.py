from collections.abc import Awaitable, Callable
from datetime import timedelta
import uuid
from app.core.config import settings
from app.core.exceptions import (
    AuthenticationFailedException,
    BankOSException,
    TenantNotFoundException,
)
from app.core.security import create_access_token, get_password_hash, verify_password
from app.modules.audit.domain.entities import AuditLog
from app.modules.audit.domain.repositories import AuditRepository
from app.modules.auth_users.application.dtos import (
    CreateUserDTO,
    LoginRequestDTO,
    TokenResponseDTO,
    UserResponseDTO,
)
from app.modules.auth_users.domain.entities import User
from app.modules.auth_users.domain.repositories import UserRepository
from app.modules.tenants.domain.entities import Tenant
from app.modules.tenants.domain.repositories import TenantRepository


class AuthenticateUserUseCase:
    """Caso de uso para autenticar usuarios en un tenant y generar su sesión JWT."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        user_repository: UserRepository,
        check_account_limit: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.tenant_repository = tenant_repository
        self.user_repository = user_repository
        self.check_account_limit = check_account_limit

    async def _resolve_tenant(self, dto: LoginRequestDTO) -> Tenant | None:
        if dto.bank_id is None:
            return await self.tenant_repository.get_by_slug(dto.tenant_slug) if dto.tenant_slug else None
        if dto.tenant_slug is not None:
            return None
        try:
            public_id = uuid.UUID(dto.bank_id.strip())
        except ValueError:
            return None
        return await self.tenant_repository.get_by_public_id(public_id)

    async def execute(self, dto: LoginRequestDTO) -> TokenResponseDTO:
        # 1. Validar existencia y estado del tenant
        tenant = await self._resolve_tenant(dto)
        if self.check_account_limit is not None:
            selector = tenant.slug if tenant else dto.tenant_slug or f"public:{dto.bank_id or ''}"
            await self.check_account_limit(selector, dto.email)
        if not tenant or not tenant.is_active:
            raise AuthenticationFailedException("Institución bancaria o credenciales no válidas")

        # 2. Validar usuario en el ámbito exclusivo del tenant
        user = await self.user_repository.get_by_email_and_tenant(
            email=dto.email.strip().lower(),
            tenant_id=tenant.id,
        )
        if not user or not user.is_active:
            raise AuthenticationFailedException("Institución bancaria o credenciales no válidas")

        # 3. Validar coincidencia de contraseña cifrada
        if not verify_password(dto.password, user.hashed_password):
            raise AuthenticationFailedException("Institución bancaria o credenciales no válidas")

        # 4. Generar JWT firmado con claims obligatorios (tenant_id, user_id, role)
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        token = create_access_token(
            tenant_id=str(tenant.id),
            user_id=str(user.id),
            role=user.role.value,  # type: ignore[arg-type]
            expires_delta=expires_delta,
        )

        return TokenResponseDTO(
            access_token=token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
        )


class CreateUserUseCase:
    """Caso de uso para que un administrador dé de alta usuarios en su tenant con auditoría."""

    def __init__(
        self,
        user_repository: UserRepository,
        audit_repository: AuditRepository,
    ) -> None:
        self.user_repository = user_repository
        self.audit_repository = audit_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: CreateUserDTO,
        actor_user_id: uuid.UUID,
    ) -> UserResponseDTO:
        # Verificar duplicidad de email dentro del mismo tenant
        normalized_email = dto.email.strip().lower()
        existing = await self.user_repository.get_by_email_and_tenant(
            email=normalized_email,
            tenant_id=tenant_id,
        )
        if existing:
            raise BankOSException(
                message=f"El correo '{normalized_email}' ya se encuentra registrado en este tenant",
                internal_code="USER_EMAIL_ALREADY_EXISTS",
                status_code=409,
            )

        hashed_password = get_password_hash(dto.password)
        user = User.create(
            tenant_id=tenant_id,
            email=normalized_email,
            hashed_password=hashed_password,
            full_name=dto.full_name,
            role=dto.role,
        )

        saved = await self.user_repository.create(user)

        # Registro de auditoría inmutable
        audit = AuditLog.create(
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action="USER_CREATED",
            previous_state=None,
            new_state={
                "user_id": str(saved.id),
                "email": saved.email,
                "full_name": saved.full_name,
                "role": saved.role.value,
                "is_active": saved.is_active,
            },
        )
        await self.audit_repository.record(audit)

        return UserResponseDTO(
            id=saved.id,
            tenant_id=saved.tenant_id,
            email=saved.email,
            full_name=saved.full_name,
            role=saved.role,
            is_active=saved.is_active,
            created_at=saved.created_at,
        )

