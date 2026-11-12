from collections.abc import AsyncGenerator
import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool, Redis

from app.core.config import settings

# Pool de conexiones asíncronas global
redis_pool: ConnectionPool | None = None


def get_redis_pool() -> ConnectionPool:
    """Obtiene o inicializa el ConnectionPool asíncrono de Redis."""
    global redis_pool
    if redis_pool is None:
        redis_pool = ConnectionPool.from_url(
            settings.async_redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return redis_pool


async def init_redis_pool() -> None:
    """Inicializa explícitamente el pool de conexiones en el inicio de la aplicación."""
    get_redis_pool()


async def close_redis_pool() -> None:
    """Cierra limpiamente el pool de conexiones al detener la aplicación."""
    global redis_pool
    if redis_pool is not None:
        await redis_pool.disconnect()
        redis_pool = None


async def get_redis() -> AsyncGenerator[Redis, None]:
    """Inyector de dependencias FastAPI para obtener un cliente asíncrono de Redis.
    
    Permite operaciones de idempotencia, caché y rate limiting en capas de aplicación.
    """
    pool = get_redis_pool()
    client = Redis(connection_pool=pool)
    try:
        yield client
    finally:
        await client.aclose()


async def check_redis_connection() -> bool:
    """Verifica la conectividad activa con el servidor Redis mediante un comando PING."""
    try:
        pool = get_redis_pool()
        client = Redis(connection_pool=pool)
        pong = await client.ping()
        await client.aclose()
        return bool(pong)
    except Exception:
        return False

