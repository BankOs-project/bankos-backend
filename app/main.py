from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from fastapi import APIRouter, Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import engine, get_db
from app.core.exceptions import setup_exception_handlers
from app.core.middlewares import CorrelationIdMiddleware
from app.core.redis import close_redis_pool, get_redis, init_redis_pool
from app.core.responses import error_response, success_response

# Configuración básica de logging
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("bankos.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Gestor del ciclo de vida de la aplicación BankOS (inicio y detención de recursos)."""
    logger.info("Iniciando BankOS Backend v%s (%s)", settings.VERSION, settings.ENVIRONMENT)
    # Inicializar pool de conexiones Redis
    await init_redis_pool()
    yield
    # Limpieza al apagar la aplicación
    logger.info("Deteniendo recursos de BankOS Backend...")
    await close_redis_pool()
    await engine.dispose()
    logger.info("Recursos liberados correctamente.")


# Inicialización de la aplicación FastAPI
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend Monolítico Modular para Sistema Bancario Multitenant como Servicio (BankOS)",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# 1. Registro de Middlewares Globales (Correlation ID y CORS)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Configuración de Exception Handlers Estandarizados (Envelope APIResponse)
setup_exception_handlers(app)

# 3. Enrutador Principal bajo prefijo /api/v1
api_v1_router = APIRouter(prefix=settings.API_V1_STR)


@api_v1_router.get(
    "/health",
    summary="Verificación de Salud de la Infraestructura",
    description="Comprueba la conectividad activa con el motor de base de datos PostgreSQL 15 y el clúster de Redis 7.",
    tags=["Salud y Monitoreo"],
)
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
):
    """Endpoint de comprobación de salud técnica."""
    db_status = "disconnected"
    redis_status = "disconnected"
    healthy = True

    # 1. Verificación de PostgreSQL
    try:
        result = await db.execute(text("SELECT 1"))
        if result.scalar() == 1:
            db_status = "connected"
    except Exception as exc:
        logger.error("Fallo al verificar PostgreSQL: %s", exc)
        healthy = False

    # 2. Verificación de Redis
    try:
        pong = await redis_client.ping()
        if pong:
            redis_status = "connected"
    except Exception as exc:
        logger.error("Fallo al verificar Redis: %s", exc)
        healthy = False

    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "database": db_status,
        "redis": redis_status,
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
    }

    if healthy:
        return success_response(
            data=payload,
            message="Todos los servicios de infraestructura están operativos",
            internal_code="HEALTH_OK",
            status_code=status.HTTP_200_OK,
        )

    return error_response(
        data=payload,
        message="Uno o más servicios de infraestructura no se encuentran disponibles",
        internal_code="INFRASTRUCTURE_UNAVAILABLE",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


# Registrar router v1 en la aplicación
app.include_router(api_v1_router)

