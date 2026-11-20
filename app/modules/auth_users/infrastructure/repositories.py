from datetime import datetime
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth_users.domain.entities import User, UserRole
from app.modules.auth_users.domain.repositories import UserRepository
from app.modules.auth_users.infrastructure.models import PersonModel, UserModel


class PostgresUserRepository(UserRepository):
    """Implementación de persistencia para usuarios bancarios con PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: UserModel) -> User:
        return User(
            id=model.id,
            tenant_id=model.tenant_id,
            email=model.email,
            hashed_password=model.hashed_password,
            full_name=model.full_name,
            role=UserRole(model.role),
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(
        self,
        user: User,
        *,
        person_id: uuid.UUID | None = None,
        email_verified_at: datetime | None = None,
    ) -> User:
        if person_id is None:
            person_id = uuid.uuid4()
            self.session.add(PersonModel(id=person_id))
            await self.session.flush()
        model = UserModel(
            id=user.id,
            tenant_id=user.tenant_id,
            person_id=person_id,
            email_verified_at=email_verified_at,
            email=user.email,
            hashed_password=user.hashed_password,
            full_name=user.full_name,
            role=user.role.value,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_entity(model)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_email_and_tenant(self, email: str, tenant_id: uuid.UUID) -> User | None:
        stmt = select(UserModel).where(
            UserModel.email == email.strip().lower(),
            UserModel.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        count_stmt = select(func.count()).select_from(UserModel).where(UserModel.tenant_id == tenant_id)
        count_res = await self.session.execute(count_stmt)
        total = count_res.scalar_one() or 0

        stmt = (
            select(UserModel)
            .where(UserModel.tenant_id == tenant_id)
            .order_by(UserModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

