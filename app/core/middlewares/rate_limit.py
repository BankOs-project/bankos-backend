"""Límites de ventana fija con contadores atómicos en Redis."""
import asyncio
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address, ip_network
import logging
from uuid import UUID

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings
from app.core.exceptions import BankOSException, RateLimitExceededException, rate_limit_response
from app.core.redis import get_redis_pool
from app.modules.auth_users.presentation.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger("bankos.rate_limit")

INCREMENT_WINDOW = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


@dataclass(frozen=True)
class RateLimitRule:
    max_requests: int
    window_seconds: int = 60


LOGIN_IP = RateLimitRule(10)
LOGIN_ACCOUNT = RateLimitRule(5)
TRANSACTION_IP = RateLimitRule(30)
TRANSACTION_USER = RateLimitRule(60)
REGISTRATION_IP = RateLimitRule(15)
REGISTRATION_MAIL = RateLimitRule(3, 900)


async def _enforce(key: str, rule: RateLimitRule, *, require_redis: bool = False) -> None:
    try:
        async with asyncio.timeout(1):
            async with Redis(connection_pool=get_redis_pool()) as redis:
                count, ttl = await redis.eval(INCREMENT_WINDOW, 1, key, rule.window_seconds)
    except (RedisError, TimeoutError):
        logger.warning("Redis no disponible para rate limiting", exc_info=True)
        if require_redis:
            raise BankOSException(
                "El registro no está disponible temporalmente", "REGISTRATION_UNAVAILABLE", 503,
            ) from None
        return

    if count > rule.max_requests:
        raise RateLimitExceededException(rule.max_requests, rule.window_seconds, max(ttl, 1))


async def enforce_login_account_limit(tenant_slug: str, email: str) -> None:
    identity = f"{tenant_slug.strip().lower()}\x00{email.strip().lower()}"
    account = sha256(identity.encode()).hexdigest()
    await _enforce(f"rl:login:account:{account}", LOGIN_ACCOUNT)


async def enforce_transaction_user_limit(
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    await _enforce(f"rl:transactions:user:{user.tenant_id}:{user.user_id}", TRANSACTION_USER)


async def enforce_registration_mail_limit(
    tenant_id: UUID, email: str, source_user_id: UUID | None = None,
) -> None:
    digest = sha256(email.encode()).hexdigest()
    await _enforce(f"rl:register:mail:{tenant_id}:{digest}", REGISTRATION_MAIL, require_redis=True)
    if source_user_id is not None:
        await _enforce(f"rl:register:source:{source_user_id}", REGISTRATION_MAIL, require_redis=True)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Aplica el límite por IP antes de leer el cuerpo o validar credenciales."""

    def __init__(self, app):
        super().__init__(app)
        self.trusted_proxies = tuple(
            ip_network(network.strip(), strict=False)
            for network in settings.RATE_LIMIT_TRUSTED_PROXIES.split(",")
            if network.strip()
        )

    def _client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if self.trusted_proxies:
            try:
                address = ip_address(peer)
                if any(address in proxy for proxy in self.trusted_proxies):
                    return str(ip_address(request.headers.get("X-Real-IP", "").strip()))
            except ValueError:
                pass
        return peer

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path.rstrip("/")
        login = f"{settings.API_V1_STR}/auth/login"
        transactions = f"{settings.API_V1_STR}/transactions"
        if path == login and request.method == "POST":
            operation, rule = "login", LOGIN_IP
        elif request.method == "POST" and path in {
            f"{settings.API_V1_STR}/auth/register", f"{settings.API_V1_STR}/auth/register/verify",
        }:
            operation, rule = "register", REGISTRATION_IP
        elif (path == transactions or path.startswith(f"{transactions}/")) and request.method != "OPTIONS":
            operation, rule = "transactions", TRANSACTION_IP
        else:
            return await call_next(request)

        try:
            await _enforce(f"rl:{operation}:ip:{self._client_ip(request)}", rule)
        except RateLimitExceededException as exc:
            return rate_limit_response(exc)
        return await call_next(request)

