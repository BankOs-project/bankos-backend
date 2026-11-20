import logging
from typing import Any
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.responses import error_response

logger = logging.getLogger("bankos.exceptions")


class BankOSException(Exception):
    """Excepción base para todos los errores de dominio y lógica de negocio de BankOS."""

    def __init__(
        self,
        message: str,
        internal_code: str = "BUSINESS_RULE_ERROR",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.internal_code = internal_code
        self.status_code = status_code
        self.data = data


class TenantNotFoundException(BankOSException):
    """Lanzada cuando un tenant solicitado no existe o está inactivo."""

    def __init__(self, message: str = "El tenant especificado no existe o no se encuentra activo", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="TENANT_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            data=data,
        )


class TenantAccessDeniedException(BankOSException):
    """Lanzada cuando se intenta acceder o modificar datos de un tenant no autorizado."""

    def __init__(self, message: str = "Acceso denegado al tenant solicitado", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="TENANT_ACCESS_DENIED",
            status_code=status.HTTP_403_FORBIDDEN,
            data=data,
        )


class AuthenticationFailedException(BankOSException):
    """Lanzada ante fallos en verificación de credenciales o token."""

    def __init__(self, message: str = "Credenciales de autenticación inválidas", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="AUTHENTICATION_FAILED",
            status_code=status.HTTP_401_UNAUTHORIZED,
            data=data,
        )


class AuthorizationFailedException(BankOSException):
    """Lanzada cuando un usuario no posee el rol requerido para una operación."""

    def __init__(self, message: str = "Permisos insuficientes para realizar esta acción", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="AUTHORIZATION_FAILED",
            status_code=status.HTTP_403_FORBIDDEN,
            data=data,
        )


class ResourceNotFoundException(BankOSException):
    """Lanzada cuando un recurso (cuenta, transacción, usuario) no es encontrado en el tenant."""

    def __init__(self, message: str = "El recurso solicitado no fue encontrado", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="RESOURCE_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            data=data,
        )


class InsufficientFundsException(BankOSException):
    """Lanzada cuando una cuenta no tiene fondos suficientes para una operación."""

    def __init__(self, message: str = "Saldo insuficiente para completar la transacción", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="INSUFFICIENT_FUNDS",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            data=data,
        )


class IdempotencyConflictException(BankOSException):
    """Lanzada cuando una operación con la misma Idempotency-Key está en proceso concurrente."""

    def __init__(self, message: str = "Transacción en proceso concurrente. Intente nuevamente en unos instantes.", data: Any = None) -> None:
        super().__init__(
            message=message,
            internal_code="IDEMPOTENCY_IN_PROGRESS",
            status_code=status.HTTP_409_CONFLICT,
            data=data,
        )


class IdempotencyKeyReusedException(BankOSException):
    def __init__(self) -> None:
        super().__init__(
            message="La clave de idempotencia ya corresponde a otro tipo de operación",
            internal_code="IDEMPOTENCY_KEY_REUSED",
            status_code=status.HTTP_409_CONFLICT,
        )


class RateLimitExceededException(BankOSException):
    """Lanzada cuando se supera el límite de peticiones permitido por IP o usuario."""

    def __init__(self, limit: int, window_seconds: int, retry_after: int) -> None:
        super().__init__(
            message=f"Límite de solicitudes excedido. Intente en {retry_after} segundos.",
            internal_code="RATE_LIMIT_EXCEEDED",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            data={"limit": limit, "window_seconds": window_seconds, "retry_after_seconds": retry_after},
        )
        self.retry_after = retry_after


def rate_limit_response(exc: RateLimitExceededException):
    response = error_response(
        message=exc.message, internal_code=exc.internal_code, status_code=exc.status_code, data=exc.data,
    )
    response.headers["Retry-After"] = str(exc.retry_after)
    return response


def setup_exception_handlers(app: FastAPI) -> None:
    """Registra manejadores de excepciones globales para garantizar el formato estándar APIResponse."""
    from app.core.middlewares.correlation_id import CORRELATION_ID_HEADER, correlation_id_ctx

    @app.exception_handler(RateLimitExceededException)
    async def rate_limit_exception_handler(_: Request, exc: RateLimitExceededException):
        return rate_limit_response(exc)

    @app.exception_handler(BankOSException)
    async def bankos_exception_handler(_: Request, exc: BankOSException):
        return error_response(
            message=exc.message,
            internal_code=exc.internal_code,
            status_code=exc.status_code,
            data=exc.data,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError):
        # Normalizar los errores de validación de FastAPI / Pydantic
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(p) for p in err.get("loc", []))
            msg = err.get("msg", "Error de validación")
            errors.append({"field": loc, "message": msg, "type": err.get("type")})

        return error_response(
            message="Error en la validación de los datos enviados",
            internal_code="VALIDATION_ERROR",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            data={"validation_errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException):
        internal_code = "HTTP_ERROR"
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            internal_code = "UNAUTHORIZED"
        elif exc.status_code == status.HTTP_403_FORBIDDEN:
            internal_code = "FORBIDDEN"
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            internal_code = "NOT_FOUND"

        return error_response(
            message=str(exc.detail),
            internal_code=internal_code,
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        correlation_id = getattr(request.state, "correlation_id", "")
        token = correlation_id_ctx.set(correlation_id)
        try:
            logger.exception("Excepción no controlada capturada en middleware global: %s", exc)
        finally:
            correlation_id_ctx.reset(token)
        response = error_response(
            message="Ocurrió un error interno en el servidor",
            internal_code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        if correlation_id:
            response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response

