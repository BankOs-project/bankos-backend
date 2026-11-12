"""
Rate Limiting en Redis por IP y por user_id para BankOS.

Estrategia: Sliding Window Counter usando INCR + EXPIRE.
  - Si el contador para la clave aún no existe, se crea con EXPIRE.
  - Si ya existe, solo se incrementa (INCR).
  - El TTL original se preserva gracias a que EXPIRE solo se llama en la primera petición.

Límites por defecto (configurables vía Settings):
  - Login:        10 peticiones / 60 segundos por IP
  - Transactions: 60 peticiones / 60 segundos por user_id
                  30 peticiones / 60 segundos por IP
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.redis import get_redis_pool

logger = logging.getLogger("bankos.rate_limit")

# ---------------------------------------------------------------------------
# Configuración de límites
# ---------------------------------------------------------------------------

@dataclass
class RateLimitRule:
    max_requests: int   # Peticiones máximas permitidas
    window_seconds: int  # Ventana de tiempo en segundos


# Reglas por patrón de path (prefijo)
RATE_LIMIT_RULES: dict[str, dict[str, RateLimitRule]] = {
    "/api/v1/auth/login": {
        "ip": RateLimitRule(max_requests=10, window_seconds=60),
    },
    "/api/v1/transactions": {
        "user": RateLimitRule(max_requests=60, window_seconds=60),
        "ip": RateLimitRule(max_requests=30, window_seconds=60),
    },
}


def _get_client_ip(request: Request) -> str:
    """Extrae la dirección IP del cliente respetando proxies reversos."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _get_matching_rule(path: str) -> dict[str, RateLimitRule] | None:
    """Retorna las reglas de rate limit que aplican al path dado."""
    for prefix, rules in RATE_LIMIT_RULES.items():
        if path.startswith(prefix):
            return rules
    return None


async def _check_and_increment(
    redis: Redis,
    key: str,
    rule: RateLimitRule,
) -> tuple[int, bool]:
    """
    Incrementa el contador en Redis y verifica si se superó el límite.
    Retorna (count_actual, limit_exceeded).
    """
    # Pipeline atómico: INCR + EXPIRE solo si es la primera petición
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    results = await pipe.execute()

    count: int = results[0]
    ttl: int = results[1]

    # Si el TTL es -1 (sin expiración) o -2 (clave no existe), establecer ventana
    if ttl < 0:
        await redis.expire(key, rule.window_seconds)

    return count, count > rule.max_requests


def _rate_limit_error_response(
    limit_type: str,
    max_requests: int,
    window_seconds: int,
    retry_after: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "internal_code": "RATE_LIMIT_EXCEEDED",
            "message": (
                f"Se superó el límite de {max_requests} peticiones por {window_seconds} segundos "
                f"({limit_type}). Intente nuevamente en {retry_after} segundos."
            ),
            "data": {
                "limit": max_requests,
                "window_seconds": window_seconds,
                "retry_after_seconds": retry_after,
            },
        },
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware de rate limiting para BankOS usando Redis como backend de contadores.

    Aplica límites configurados por patrón de path, diferenciados por:
    - IP del cliente (para todos los endpoints protegidos)
    - user_id extraído del JWT (para endpoints de transacciones)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        rules = _get_matching_rule(path)

        if not rules:
            return await call_next(request)

        client_ip = _get_client_ip(request)
        user_id = getattr(request.state, "user_id", None)

        try:
            redis_pool = get_redis_pool()
            redis = Redis(connection_pool=redis_pool)

            # 1. Verificar límite por IP
            if "ip" in rules:
                ip_rule = rules["ip"]
                ip_key = f"rl:ip:{path}:{client_ip}"
                count, exceeded = await _check_and_increment(redis, ip_key, ip_rule)
                if exceeded:
                    logger.warning("Rate limit by IP exceeded: ip=%s path=%s count=%d", client_ip, path, count)
                    ttl = await redis.ttl(ip_key)
                    return _rate_limit_error_response(
                        limit_type=f"IP {client_ip}",
                        max_requests=ip_rule.max_requests,
                        window_seconds=ip_rule.window_seconds,
                        retry_after=max(ttl, 1),
                    )

            # 2. Verificar límite por user_id (solo si está disponible en el estado)
            if "user" in rules and user_id:
                user_rule = rules["user"]
                user_key = f"rl:user:{path}:{user_id}"
                count, exceeded = await _check_and_increment(redis, user_key, user_rule)
                if exceeded:
                    logger.warning("Rate limit by user exceeded: user_id=%s path=%s count=%d", user_id, path, count)
                    ttl = await redis.ttl(user_key)
                    return _rate_limit_error_response(
                        limit_type=f"usuario {user_id}",
                        max_requests=user_rule.max_requests,
                        window_seconds=user_rule.window_seconds,
                        retry_after=max(ttl, 1),
                    )

            await redis.aclose()

        except Exception as exc:
            # Rate limiting no debe bloquear el servicio si Redis falla
            logger.error("Rate limit check failed (Redis error), allowing request: %s", exc)

        return await call_next(request)

