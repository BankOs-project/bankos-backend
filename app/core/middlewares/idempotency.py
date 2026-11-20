"""Idempotencia de operaciones financieras con PostgreSQL y caché Redis."""
import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
import json
import logging
import uuid

from fastapi import Depends, Header, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.exceptions import (
    BankOSException,
    IdempotencyConflictException,
    IdempotencyKeyReusedException,
)
from app.core.redis import get_redis
from app.modules.auth_users.presentation.dependencies import AuthenticatedUser, get_current_user
from app.modules.transactions.infrastructure.models import IdempotencyReceiptModel

logger = logging.getLogger("bankos.idempotency")

IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_WINDOW = timedelta(hours=24)
CACHE_TIMEOUT_SECONDS = 1


class IdempotencyContext:
    def __init__(
        self,
        session: AsyncSession,
        redis: Redis,
        user: AuthenticatedUser,
        key: uuid.UUID,
        path: str,
    ) -> None:
        self.session = session
        self.redis = redis
        self.user = user
        self.key = key
        self.path = path
        self.cache_key = f"idemp:receipt:v1:{user.tenant_id}:{user.user_id}:{key}"

    @property
    def idempotency_key(self) -> str:
        return str(self.key)

    def _replay(self, path: str, status_code: int, body: str) -> Response:
        if path != self.path:
            raise IdempotencyKeyReusedException()
        return Response(content=body, status_code=status_code, media_type="application/json")

    async def replay_or_lock(self, body_key: str | None) -> Response | None:
        if body_key is not None and body_key.lower() != self.idempotency_key:
            raise BankOSException(
                message="La clave de idempotencia del cuerpo debe coincidir con Idempotency-Key",
                internal_code="IDEMPOTENCY_KEY_MISMATCH",
                status_code=422,
            )

        try:
            async with asyncio.timeout(CACHE_TIMEOUT_SECONDS):
                cached = await self.redis.get(self.cache_key)
            if cached is not None:
                receipt = json.loads(cached)
                if datetime.fromisoformat(receipt["expires_at"]) > datetime.now(timezone.utc):
                    return self._replay(receipt["path"], receipt["status_code"], receipt["body"])
        except (RedisError, TimeoutError, ValueError, TypeError, KeyError):
            logger.warning("No se pudo leer el comprobante desde Redis", exc_info=True)

        # PostgreSQL libera el lock al confirmar, revertir o perder la conexión.
        # Redis puede perder su contenido sin perder la exclusión ni el comprobante.
        identity = self.user.tenant_id.bytes + self.user.user_id.bytes + self.key.bytes
        lock_key = blake2b(identity, digest_size=8).digest()
        lock_id = int.from_bytes(lock_key, byteorder="big", signed=True)
        acquired = await self.session.scalar(select(func.pg_try_advisory_xact_lock(lock_id)))

        receipt = await self.session.scalar(
            select(IdempotencyReceiptModel).where(
                IdempotencyReceiptModel.tenant_id == self.user.tenant_id,
                IdempotencyReceiptModel.user_id == self.user.user_id,
                IdempotencyReceiptModel.key == self.key,
                IdempotencyReceiptModel.expires_at > func.clock_timestamp(),
            )
        )
        if receipt is None:
            if not acquired:
                raise IdempotencyConflictException()
            return None

        response = self._replay(receipt.path, receipt.status_code, receipt.body)
        receipt_expires_at = receipt.expires_at
        await self.session.rollback()  # El camino de lectura ya puede liberar el lock.
        await self._cache(response, receipt_expires_at)
        return response

    async def confirm(self, response: Response, *, expires_at: datetime | None = None) -> Response:
        if expires_at is None:
            db_time = await self.session.scalar(select(func.clock_timestamp()))
            expires_at = db_time + IDEMPOTENCY_WINDOW

        receipt = insert(IdempotencyReceiptModel).values(
            tenant_id=self.user.tenant_id,
            user_id=self.user.user_id,
            key=self.key,
            path=self.path,
            status_code=response.status_code,
            body=response.body.decode("utf-8"),
            expires_at=expires_at,
        )
        await self.session.execute(
            receipt.on_conflict_do_update(
                index_elements=["tenant_id", "user_id", "key"],
                set_={
                    "path": receipt.excluded.path,
                    "status_code": receipt.excluded.status_code,
                    "body": receipt.excluded.body,
                    "expires_at": receipt.excluded.expires_at,
                },
            )
        )
        await self.session.commit()
        await self._cache(response, expires_at)
        return response

    async def _cache(self, response: Response, expires_at: datetime) -> None:
        if expires_at <= datetime.now(timezone.utc):
            return
        payload = json.dumps({
            "path": self.path,
            "status_code": response.status_code,
            "body": response.body.decode("utf-8"),
            "expires_at": expires_at.isoformat(),
        })
        try:
            async with asyncio.timeout(CACHE_TIMEOUT_SECONDS):
                await self.redis.set(self.cache_key, payload, pxat=int(expires_at.timestamp() * 1000))
        except (RedisError, TimeoutError):
            logger.warning("No se pudo cachear el comprobante confirmado", exc_info=True)


async def get_idempotency_context(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    key: str | None = Header(None, alias=IDEMPOTENCY_HEADER),
) -> AsyncIterator[IdempotencyContext]:
    if not key or not key.strip():
        raise BankOSException(
            message="El encabezado Idempotency-Key es obligatorio",
            internal_code="MISSING_IDEMPOTENCY_KEY",
            status_code=400,
        )

    key = key.strip()
    try:
        parsed_key = uuid.UUID(key)
        if parsed_key.version != 4 or str(parsed_key) != key.lower():
            raise ValueError
    except ValueError as exc:
        raise BankOSException(
            message="Idempotency-Key debe ser un UUID v4 en su formato estándar",
            internal_code="INVALID_IDEMPOTENCY_KEY",
            status_code=422,
        ) from exc

    async with async_session_factory() as session:
        yield IdempotencyContext(session, redis, current_user, parsed_key, request.url.path)

