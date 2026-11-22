"""Perfil privado y verificación de correo para la relación bancaria de la sesión."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.exceptions import AuthenticationFailedException, BankOSException
from app.core.middlewares.rate_limit import enforce_profile_email_limit
from app.core.security import verify_password
from app.modules.auth_users.application.dtos import (
    StartProfileEmailChangeDTO, UpdateProfileDTO, VerifyProfileEmailChangeDTO,
)
from app.modules.auth_users.infrastructure.models import ProfileEmailChangeModel, UserModel, UserProfileModel
from app.modules.auth_users.infrastructure.registration_mail import send_profile_email_code
from app.modules.auth_users.presentation.dependencies import AuthenticatedUser
from app.modules.tenants.infrastructure.models import TenantModel

EMAIL_CHANGE_TTL_SECONDS = 1200


class ProfileService:
    def __init__(self, session: AsyncSession, actor: AuthenticatedUser) -> None:
        self.session = session
        self.actor = actor

    async def _user(self, *, lock: bool = False) -> UserModel:
        query = select(UserModel).join(TenantModel, TenantModel.id == UserModel.tenant_id).where(
            UserModel.id == self.actor.user_id,
            UserModel.tenant_id == self.actor.tenant_id,
            UserModel.is_active.is_(True),
            TenantModel.is_active.is_(True),
        )
        if lock:
            query = query.with_for_update(of=UserModel)
        user = await self.session.scalar(query)
        if user is None:
            raise AuthenticationFailedException("La sesión no corresponde a un usuario activo")
        return user

    def _scope(self, model):
        return model.user_id == self.actor.user_id, model.tenant_id == self.actor.tenant_id

    async def _profile(self) -> UserProfileModel | None:
        return await self.session.scalar(select(UserProfileModel).where(*self._scope(UserProfileModel)))

    def _new_profile(self) -> UserProfileModel:
        profile = UserProfileModel(user_id=self.actor.user_id, tenant_id=self.actor.tenant_id)
        self.session.add(profile)
        return profile

    @staticmethod
    def _payload(user: UserModel, profile: UserProfileModel | None) -> dict:
        return {
            "id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
            "document_type": profile.document_type if profile else None,
            "document_number": profile.document_number if profile else None,
            "phone": profile.phone if profile else None,
            "address": profile.address if profile else None,
            "photo_url": f"{settings.API_V1_STR}/auth/me/photo" if profile and profile.photo_type else None,
        }

    async def get(self) -> dict:
        return self._payload(await self._user(), await self._profile())

    async def update(self, dto: UpdateProfileDTO) -> dict:
        user = await self._user(lock=True)
        profile = await self._profile()
        changes = dto.model_dump(exclude_unset=True)
        if "full_name" in changes:
            user.full_name = changes.pop("full_name")
        if changes:
            if profile is None:
                profile = self._new_profile()
            for field, value in changes.items():
                setattr(profile, field, value)
            if (profile.document_type is None) != (profile.document_number is None):
                raise BankOSException(
                    "Indica o elimina el tipo y el número de documento juntos", "PROFILE_DOCUMENT_INCOMPLETE", 422,
                )
        await self.session.commit()
        return self._payload(user, profile)

    async def get_photo(self) -> tuple[str, bytes]:
        await self._user()
        result = await self.session.execute(
            select(UserProfileModel.photo_type, UserProfileModel.photo_content)
            .where(*self._scope(UserProfileModel), UserProfileModel.photo_type.is_not(None))
        )
        photo = result.one_or_none()
        if photo is None:
            raise BankOSException("El perfil no tiene foto", "PROFILE_PHOTO_NOT_FOUND", 404)
        return photo.photo_type, photo.photo_content

    async def set_photo(self, media_type: str | None, content: bytes | None) -> dict:
        user = await self._user(lock=True)
        profile = await self._profile()
        if profile is None and content is not None:
            profile = self._new_profile()
        if profile is not None:
            profile.photo_type = media_type
            profile.photo_content = content
        await self.session.commit()
        return self._payload(user, profile)

    async def start_email_change(self, dto: StartProfileEmailChangeDTO) -> UUID:
        email = dto.new_email.lower()
        await enforce_profile_email_limit(self.actor.tenant_id, self.actor.user_id, email=email)
        user = await self._user(lock=True)
        if not await run_in_threadpool(verify_password, dto.current_password.get_secret_value(), user.hashed_password):
            raise AuthenticationFailedException()
        if email == user.email.lower():
            raise BankOSException("Indica un correo distinto al correo actual", "PROFILE_EMAIL_UNCHANGED", 409)

        code = token_urlsafe(32)
        request_id = uuid4()
        challenge = await self.session.scalar(
            select(ProfileEmailChangeModel).where(*self._scope(ProfileEmailChangeModel))
        )
        if challenge is None:
            challenge = ProfileEmailChangeModel(user_id=user.id, tenant_id=user.tenant_id)
            self.session.add(challenge)
        challenge.request_id = request_id
        challenge.previous_email = user.email
        challenge.new_email = email
        challenge.code_digest = sha256(code.encode()).hexdigest()
        challenge.expires_at = datetime.now(timezone.utc) + timedelta(seconds=EMAIL_CHANGE_TTL_SECONDS)
        await self.session.commit()
        try:
            await send_profile_email_code(email, code)
        except BankOSException:
            await self.session.execute(
                delete(ProfileEmailChangeModel).where(
                    *self._scope(ProfileEmailChangeModel), ProfileEmailChangeModel.request_id == request_id,
                )
            )
            await self.session.commit()
            raise
        return request_id

    async def verify_email_change(self, dto: VerifyProfileEmailChangeDTO) -> dict:
        await enforce_profile_email_limit(self.actor.tenant_id, self.actor.user_id)
        user = await self._user(lock=True)
        challenge = await self.session.scalar(
            select(ProfileEmailChangeModel).where(
                *self._scope(ProfileEmailChangeModel), ProfileEmailChangeModel.request_id == dto.request_id,
            )
        )
        now = datetime.now(timezone.utc)
        submitted_digest = sha256(dto.email_code.get_secret_value().encode()).hexdigest()
        if (
            challenge is None or challenge.expires_at <= now or challenge.previous_email != user.email
            or not compare_digest(challenge.code_digest, submitted_digest)
        ):
            raise BankOSException("La verificación del correo no es válida", "PROFILE_EMAIL_INVALID", 400)

        user.email = challenge.new_email
        user.email_verified_at = now
        await self.session.delete(challenge)
        profile = await self._profile()
        try:
            await self.session.commit()
        except IntegrityError as exc:
            if exc.orig.sqlstate != "23505":
                raise
            await self.session.rollback()
            raise BankOSException("El correo no está disponible en este banco", "PROFILE_EMAIL_UNAVAILABLE_IN_BANK", 409) from None
        return self._payload(user, profile)
