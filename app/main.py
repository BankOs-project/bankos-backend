from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import os
from fastapi import APIRouter, Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import engine, get_db
from app.core.database_setup import prepare_schema, require_audit_append_only
from app.core.exceptions import setup_exception_handlers
from app.core.middlewares import (
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    install_correlation_filter,
)
from app.core.redis import close_redis_pool, get_redis, init_redis_pool
from app.core.responses import error_response, success_response

# Importación de modelos ORM para registro en Base.metadata (orden FK-safe)
from app.modules.tenants.infrastructure.models import TenantModel          # noqa: F401
from app.modules.auth_users.infrastructure.models import UserModel          # noqa: F401
from app.modules.accounts.infrastructure.models import AccountModel         # noqa: F401
from app.modules.transactions.infrastructure.models import TransactionModel # noqa: F401
from app.modules.audit.infrastructure.models import AuditLogModel           # noqa: F401

# Routers modulares
from app.modules.tenants.presentation.router import router as tenants_router
from app.modules.tenants.presentation.router import directory_router
from app.modules.auth_users.presentation.router import router as auth_router
from app.modules.accounts.presentation.router import router as accounts_router
from app.modules.transactions.presentation.router import router as transactions_router
from app.modules.audit.presentation.router import router as audit_router

# ---------------------------------------------------------------------------
# Configuración de logging con X-Correlation-ID inyectado en cada línea
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [cid=%(correlation_id)s] %(name)s: %(message)s",
)
install_correlation_filter()   # Registra CorrelationIdFilter en root + bankos logger
logger = logging.getLogger("bankos.main")


# ---------------------------------------------------------------------------
# Ciclo de vida de la aplicación
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Gestiona la inicialización y el cierre ordenado de todos los recursos de BankOS."""
    logger.info("Iniciando BankOS Backend v%s [%s]", settings.VERSION, settings.ENVIRONMENT)

    # 1. Inicializar pool de conexiones Redis
    await init_redis_pool()
    logger.info("Pool de Redis inicializado.")

    # 2. Crear todas las tablas en PostgreSQL si aun no existen (DDL auto en startup)
    managed_schema = settings.DATABASE_MANAGED_SCHEMA
    async with engine.begin() as conn:
        if managed_schema:
            try:
                await require_audit_append_only(conn)
            except Exception as exc:
                logger.warning("Verificación de rol audit_logs append-only no cumplida: %s", exc)
            await conn.execute(text("SELECT public_id, directory_enabled, logo_url FROM public.tenants LIMIT 0"))
            await conn.execute(text("""
                SELECT actor_user_id, failure_code, transfer_mode, transfer_reference FROM public.transactions LIMIT 0
            """))
            await conn.execute(text("SELECT id FROM public.transfer_quotes LIMIT 0"))
            await conn.execute(text("SELECT key FROM public.transaction_idempotency_receipts LIMIT 0"))
            await conn.execute(text("SELECT person_id, email_verified_at FROM public.users LIMIT 0"))
            await conn.execute(text("SELECT id FROM public.registration_challenges LIMIT 0"))
            await conn.execute(text("""
                SELECT reactivated_at, can_deactivate_at, requires_admin_reactivation
                FROM public.accounts LIMIT 0
            """))
        else:
            await prepare_schema(conn)
            try:
                await require_audit_append_only(conn)
            except Exception as exc:
                logger.info("Auditoría en modo estándar (desarrollo/PaaS): %s", exc)
    logger.info("Esquemas de base de datos sincronizados correctamente.")

    yield

    # 3. Cierre ordenado de recursos
    logger.info("Deteniendo BankOS Backend...")
    await close_redis_pool()
    await engine.dispose()
    logger.info("Recursos liberados correctamente.")


# ---------------------------------------------------------------------------
# Inicialización de la aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Backend Monolítico Modular para Sistema Bancario Multitenant como Servicio (BankOS). "
        "Soporta múltiples instituciones financieras aisladas, operaciones multimoneda, "
        "idempotencia, rate limiting y trazabilidad completa."
    ),
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middlewares (orden de registro = orden de ejecución inverso en Starlette)
# 1° se ejecuta el último en registrarse → CorrelationId debe estar al final
# para que el cid esté disponible antes de que RateLimit lo use.
# ---------------------------------------------------------------------------
app.add_middleware(RateLimitMiddleware)      # 2° en ejecutarse: aplica límites
app.add_middleware(CorrelationIdMiddleware)  # 1° en ejecutarse: asigna cid
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Exception Handlers estandarizados (Envelope APIResponse)
# ---------------------------------------------------------------------------
setup_exception_handlers(app)

# ---------------------------------------------------------------------------
# Enrutador principal /api/v1
# ---------------------------------------------------------------------------
api_v1_router = APIRouter(prefix=settings.API_V1_STR)


@api_v1_router.get(
    "/health",
    summary="Verificación de Salud de la Infraestructura",
    description=(
        "Comprueba la conectividad activa con PostgreSQL 15 y Redis 7. "
        "Retorna el estado de cada servicio bajo el envelope APIResponse."
    ),
    tags=["Salud y Monitoreo"],
)
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
):
    """Verificación técnica de disponibilidad de la infraestructura."""
    db_status = "disconnected"
    redis_status = "disconnected"
    healthy = True

    try:
        result = await db.execute(text("SELECT 1"))
        if result.scalar() == 1:
            db_status = "connected"
    except Exception as exc:
        logger.error("Fallo al verificar PostgreSQL: %s", exc)
        healthy = False

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


# Registrar routers modulares en orden lógico
api_v1_router.include_router(tenants_router)        # /api/v1/tenants
api_v1_router.include_router(directory_router)      # /api/v1/banks
api_v1_router.include_router(auth_router)           # /api/v1/auth
api_v1_router.include_router(accounts_router)       # /api/v1/accounts
api_v1_router.include_router(transactions_router)   # /api/v1/transactions
api_v1_router.include_router(audit_router)          # /api/v1/audit-logs

app.include_router(api_v1_router)
