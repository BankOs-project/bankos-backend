"""Alta por correo y vinculación explícita de relaciones bancarias."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import AuthenticationFailedException, BankOSException
from app.core.middlewares.rate_limit import enforce_registration_mail_limit
from app.core.security import get_password_hash, verify_password
from app.modules.auth_users.application.dtos import StartRegistrationDTO, VerifyRegistrationDTO
from app.modules.auth_users.domain.entities import User, UserRole
from app.modules.auth_users.infrastructure.models import RegistrationChallengeModel, UserModel
from app.modules.auth_users.infrastructure.registration_mail import send_registration_code
from app.modules.auth_users.infrastructure.repositories import PostgresUserRepository
from app.modules.auth_users.presentation.dependencies import AuthenticatedUser
from app.modules.tenants.infrastructure.models import TenantModel
from app.modules.tenants.infrastructure.repositories import PostgresTenantRepository


REGISTRATION_TTL_SECONDS = 1200


def _digest(code: str) -> str:
    return sha256(code.encode()).hexdigest()


def _invalid_registration() -> BankOSException:
    return BankOSException("La verificación de registro no es válida", "REGISTRATION_INVALID", 400)


class RegistrationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _active_source(self, actor: AuthenticatedUser) -> UserModel | None:
        return await self.session.scalar(
            select(UserModel).join(TenantModel, TenantModel.id == UserModel.tenant_id).where(
                UserModel.id == actor.user_id,
                UserModel.tenant_id == actor.tenant_id,
                UserModel.is_active.is_(True),
                TenantModel.is_active.is_(True),
            )
        )

    async def start(self, dto: StartRegistrationDTO, actor: AuthenticatedUser | None) -> uuid.UUID:
        tenant = await PostgresTenantRepository(self.session).get_by_public_id(dto.bank_id)
        if tenant is None:
            raise _invalid_registration()

        email = dto.email.strip().lower()
        await enforce_registration_mail_limit(tenant.id, email, actor.user_id if actor else None)

        source = await self._active_source(actor) if actor else None
        if actor is None and dto.source_password is not None:
            raise AuthenticationFailedException()
        if actor is not None and (
            source is None or dto.source_password is None or not await run_in_threadpool(
                verify_password, dto.source_password.get_secret_value(), source.hashed_password,
            )
        ):
            raise AuthenticationFailedException()
        if source is not None and source.tenant_id == tenant.id:
            raise _invalid_registration()

        email_code = token_urlsafe(32)
        source_code = token_urlsafe(32) if source else None
        challenge = RegistrationChallengeModel(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=email,
            email_digest=_digest(email_code),
            source_user_id=source.id if source else None,
            source_email=source.email if source else None,
            source_digest=_digest(source_code) if source_code else None,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=REGISTRATION_TTL_SECONDS),
        )
        self.session.add(challenge)
        await self.session.commit()
        try:
            await send_registration_code(email, email_code)
            if source is not None and source_code is not None:
                await send_registration_code(source.email, source_code, source=True)
        except BankOSException:
            challenge.consumed_at = datetime.now(timezone.utc)
            await self.session.commit()
            raise
        return challenge.id

    async def verify(self, dto: VerifyRegistrationDTO, actor: AuthenticatedUser | None) -> None:
        challenge = await self.session.scalar(
            select(RegistrationChallengeModel)
            .join(TenantModel, TenantModel.id == RegistrationChallengeModel.tenant_id)
            .where(
                RegistrationChallengeModel.id == dto.registration_id,
                TenantModel.directory_enabled.is_(True),
                TenantModel.is_active.is_(True),
            )
            .with_for_update(of=RegistrationChallengeModel)
        )
        now = datetime.now(timezone.utc)
        if (
            challenge is None
            or challenge.consumed_at is not None
            or challenge.expires_at <= now
            or challenge.source_user_id != (actor.user_id if actor else None)
            or not compare_digest(challenge.email_digest, _digest(dto.email_code.get_secret_value()))
        ):
            raise _invalid_registration()

        source = None
        if actor is not None:
            source = await self._active_source(actor)
            if (
                source is None
                or source.email != challenge.source_email
                or challenge.source_digest is None
                or dto.source_email_code is None
                or not compare_digest(challenge.source_digest, _digest(dto.source_email_code.get_secret_value()))
            ):
                raise _invalid_registration()

        password_hash = await run_in_threadpool(get_password_hash, dto.password.get_secret_value())
        user = User.create(
            tenant_id=challenge.tenant_id,
            email=challenge.email,
            hashed_password=password_hash,
            full_name=dto.full_name,
            role=UserRole.CLIENT,
        )
        challenge.consumed_at = now
        try:
            async with self.session.begin_nested():
                await PostgresUserRepository(self.session).create(
                    user, person_id=source.person_id if source else None, email_verified_at=now,
                )
                if source is not None and source.email_verified_at is None:
                    await self.session.execute(text("""
                        UPDATE public.users SET email_verified_at = :verified_at
                        WHERE id = :id AND tenant_id = :tenant_id AND email_verified_at IS NULL
                    """), {"verified_at": now, "id": source.id, "tenant_id": source.tenant_id})
        except IntegrityError as exc:
            if exc.orig.sqlstate != "23505":
                raise
            await self.session.commit()
            raise _invalid_registration() from None
        await self.session.commit()
