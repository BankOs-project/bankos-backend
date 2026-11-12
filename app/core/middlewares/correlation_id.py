"""
Middleware de trazabilidad X-Correlation-ID para BankOS.

Lee o genera un UUID por cada petición HTTP y lo propaga a través de:
1. ContextVar asíncrona  → disponible en cualquier capa de la aplicación (use_cases, repos, servicios)
2. request.state.correlation_id  → disponible en handlers FastAPI
3. Encabezado de respuesta X-Correlation-ID  → visibilidad en cliente móvil/frontend
4. Logging.Filter  → aparece en TODOS los mensajes de log automáticamente como campo [cid=...]
"""
import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# ContextVar asíncrona — el único punto de verdad del Correlation ID activo
# ---------------------------------------------------------------------------
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

CORRELATION_ID_HEADER = "X-Correlation-ID"


def get_correlation_id() -> str:
    """Retorna el Correlation ID activo para la petición en curso (o cadena vacía)."""
    return correlation_id_ctx.get()


# ---------------------------------------------------------------------------
# Filter de logging: inyecta el cid en cada LogRecord automáticamente
# ---------------------------------------------------------------------------

class CorrelationIdFilter(logging.Filter):
    """Agrega el atributo `correlation_id` a cada registro de log.

    Esto permite incluirlo en el formato del handler:
        format = "%(asctime)s [%(levelname)s] [cid=%(correlation_id)s] %(name)s: %(message)s"
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id() or "-"
        return True


def install_correlation_filter(logger_name: str = "bankos") -> None:
    """Instala el CorrelationIdFilter en el logger raíz de BankOS y en el root logger."""
    cid_filter = CorrelationIdFilter()
    # Root logger de la aplicación
    bankos_logger = logging.getLogger(logger_name)
    if not any(isinstance(f, CorrelationIdFilter) for f in bankos_logger.filters):
        bankos_logger.addFilter(cid_filter)
    # Root logger para capturar logs de librerías (SQLAlchemy, uvicorn, etc.)
    root_logger = logging.getLogger()
    if not any(isinstance(f, CorrelationIdFilter) for f in root_logger.filters):
        root_logger.addFilter(cid_filter)


# ---------------------------------------------------------------------------
# Middleware HTTP
# ---------------------------------------------------------------------------

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware transversal para trazabilidad de peticiones HTTP en BankOS.

    Lee el encabezado 'X-Correlation-ID' de la solicitud entrante si fue
    proporcionado por el cliente. Si no existe o está vacío, genera un UUIDv4.
    Inyecta el identificador en:
      1. ContextVar (para todos los logs y servicios asíncronos)
      2. request.state.correlation_id (para handlers de FastAPI)
      3. Encabezado de respuesta 'X-Correlation-ID' (para el cliente móvil)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header_val = request.headers.get(CORRELATION_ID_HEADER, "").strip()
        correlation_id = header_val if header_val else str(uuid.uuid4())

        # Guardar en ContextVar asíncrona y en request.state
        token = correlation_id_ctx.set(correlation_id)
        request.state.correlation_id = correlation_id

        try:
            response = await call_next(request)
        finally:
            # Restaurar el contexto al finalizar la petición
            correlation_id_ctx.reset(token)

        # Siempre devolver el header en la respuesta
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
