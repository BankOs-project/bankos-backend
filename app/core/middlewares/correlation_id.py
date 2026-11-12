import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Variable de contexto asíncrono para propagar el Correlation ID en hilos/tareas asíncronas
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

CORRELATION_ID_HEADER = "X-Correlation-ID"


def get_correlation_id() -> str:
    """Retorna el Correlation ID de la petición en curso o una cadena vacía."""
    return correlation_id_ctx.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware transversal para trazabilidad de peticiones HTTP en BankOS.
    
    Lee el encabezado 'X-Correlation-ID' de la solicitud entrante si fue proporcionado por el cliente.
    Si no existe, genera un nuevo UUIDv4. Inyecta el identificador en:
    1. Variable de contexto (para logs de auditoría y servicios internos)
    2. request.state.correlation_id (para handlers de FastAPI)
    3. Encabezado de respuesta 'X-Correlation-ID' (para trazabilidad en el cliente/móvil)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header_val = request.headers.get(CORRELATION_ID_HEADER)
        
        # Si el cliente no envió el header o es inválido, generar UUIDv4
        correlation_id = header_val.strip() if header_val and header_val.strip() else str(uuid.uuid4())

        # Guardar en contexto asíncrono y en el estado de la petición
        token = correlation_id_ctx.set(correlation_id)
        request.state.correlation_id = correlation_id

        try:
            response = await call_next(request)
        finally:
            # Restaurar el contexto asíncrono
            correlation_id_ctx.reset(token)

        # Devolver siempre el encabezado en la respuesta HTTP
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response

