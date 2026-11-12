from typing import Any, Generic, TypeVar
from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

DataType = TypeVar("DataType")


class APIResponse(BaseModel, Generic[DataType]):
    """Esquema estándar de envoltura (Envelope) para todas las respuestas de la API en BankOS."""

    success: bool = Field(
        default=True,
        description="Indica si la solicitud fue procesada con éxito o presentó un error",
    )
    internal_code: str = Field(
        description="Código de estado interno del sistema para trazabilidad y clientes móviles",
        examples=["SUCCESS", "RESOURCE_NOT_FOUND", "AUTHENTICATION_FAILED"],
    )
    message: str = Field(
        description="Mensaje descriptivo legible sobre el resultado de la operación",
        examples=["Operación realizada exitosamente"],
    )
    data: DataType | None = Field(
        default=None,
        description="Cuerpo de datos devuelto por la operación, o null en caso de error",
    )


def success_response(
    data: Any = None,
    message: str = "Operación realizada exitosamente",
    internal_code: str = "SUCCESS",
    status_code: int = status.HTTP_200_OK,
) -> JSONResponse:
    """Helper para construir una respuesta HTTP JSON exitosa bajo el contrato APIResponse."""
    payload = APIResponse(
        success=True,
        internal_code=internal_code,
        message=message,
        data=data,
    ).model_dump(mode="json")

    return JSONResponse(status_code=status_code, content=payload)


def error_response(
    message: str,
    internal_code: str = "INTERNAL_ERROR",
    status_code: int = status.HTTP_400_BAD_REQUEST,
    data: Any = None,
) -> JSONResponse:
    """Helper para construir una respuesta HTTP JSON de error bajo el contrato APIResponse."""
    payload = APIResponse(
        success=False,
        internal_code=internal_code,
        message=message,
        data=data,
    ).model_dump(mode="json")

    return JSONResponse(status_code=status_code, content=payload)

