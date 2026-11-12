"""
Dependencia de idempotencia basada en Redis para operaciones financieras mutables en BankOS.

Flujo:
1. Lee el header Idempotency-Key  → 400 si falta.
2. Construye la clave Redis: idemp:{tenant_id}:{user_id}:{idempotency_key}
3. SET NX con valor "PROCESSING" y TTL 24h:
   - Falla NX → clave existe. Lee el valor actual:
       "PROCESSING"  → 409 Conflict  (misma petición en curso)
       JSON payload  → retorna la respuesta cacheada exactamente (status + body)
4. Si NX fue exitoso, almacena token para que el router lo llame con `store_response()`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Depends, Header, Request, Response
from redis.asyncio import Redis

from app.core.redis import get_redis
from app.core.responses import error_response

logger = logging.getLogger("bankos.idempotency")

IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_TTL_SECONDS = 86_400  # 24 horas
PROCESSING_VALUE = "PROCESSING"


def _build_key(tenant_id: str, user_id: str, idempotency_key: str) -> str:
    return f"idemp:{tenant_id}:{user_id}:{idempotency_key}"


class IdempotencyContext:
    """Objeto que el router usa para cachear la respuesta una vez procesada."""

    def __init__(
        self,
        redis: Redis,
        redis_key: str,
        idempotency_key: str,
        cached_response: Response | None,
    ) -> None:
        self.redis = redis
        self.redis_key = redis_key
        self.idempotency_key = idempotency_key
        self.cached_response = cached_response  # None → hay que ejecutar el caso de uso

    async def store_response(self, status_code: int, body: dict[str, Any]) -> None:
        """Persiste el resultado final en Redis para peticiones futuras con la misma clave."""
        payload = json.dumps({"status_code": status_code, "body": body})
        await self.redis.setex(self.redis_key, IDEMPOTENCY_TTL_SECONDS, payload)


async def get_idempotency_context(
    request: Request,
    redis: Redis = Depends(get_redis),
) -> IdempotencyContext:
    """
    Dependencia FastAPI que implementa el protocolo completo de idempotencia en Redis.
    Inyectar en endpoints POST mutables.
    """
    # 1. Leer clave de idempotencia del header (obligatorio)
    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
    if not idempotency_key:
        from fastapi.responses import JSONResponse
        raise _missing_key_error()

    # 2. Extraer identidad del request.state (puesta por get_current_user en el router)
    #    Usamos strings vacíos como fallback; si no hay usuario el endpoint lo rechazará antes.
    tenant_id = str(getattr(request.state, "tenant_id", "anonymous"))
    user_id = str(getattr(request.state, "user_id", "anonymous"))

    redis_key = _build_key(tenant_id, user_id, idempotency_key)

    # 3. Intentar reservar la clave atómicamente (SET NX EX)
    acquired = await redis.set(redis_key, PROCESSING_VALUE, ex=IDEMPOTENCY_TTL_SECONDS, nx=True)

    if acquired:
        # Clave recién creada → proceder con el caso de uso normalmente
        return IdempotencyContext(
            redis=redis,
            redis_key=redis_key,
            idempotency_key=idempotency_key,
            cached_response=None,
        )

    # 4. La clave ya existía → leer su estado actual
    existing_value: str | None = await redis.get(redis_key)

    if existing_value == PROCESSING_VALUE or existing_value is None:
        # En proceso concurrente → 409 Conflict
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "internal_code": "IDEMPOTENCY_IN_PROGRESS",
                "message": (
                    "Esta transaccion ya se encuentra en proceso de ejecucion. "
                    "Espere a que concluya antes de reintentar."
                ),
                "data": {"idempotency_key": idempotency_key},
            },
        )

    # 5. Respuesta cacheada (COMPLETED) → deserializar y devolver
    try:
        cached = json.loads(existing_value)
        from fastapi.responses import JSONResponse
        cached_resp = JSONResponse(
            status_code=cached["status_code"],
            content=cached["body"],
        )
        return IdempotencyContext(
            redis=redis,
            redis_key=redis_key,
            idempotency_key=idempotency_key,
            cached_response=cached_resp,
        )
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Idempotency cache corrupted for key=%s: %s", redis_key, exc)
        # Si el caché está corrupto, permitir re-ejecución borrando la clave
        await redis.delete(redis_key)
        await redis.set(redis_key, PROCESSING_VALUE, ex=IDEMPOTENCY_TTL_SECONDS, nx=True)
        return IdempotencyContext(
            redis=redis,
            redis_key=redis_key,
            idempotency_key=idempotency_key,
            cached_response=None,
        )


def _missing_key_error():
    """Genera la excepción estándar cuando falta el header Idempotency-Key."""
    from fastapi import HTTPException
    return HTTPException(
        status_code=400,
        detail={
            "success": False,
            "internal_code": "MISSING_IDEMPOTENCY_KEY",
            "message": (
                f"El encabezado '{IDEMPOTENCY_HEADER}' es obligatorio para operaciones financieras mutables. "
                "Genera un UUID v4 en el cliente y reenvíalo en cada reintento."
            ),
            "data": None,
        },
    )

