from datetime import datetime
from typing import Any
import uuid
from pydantic import BaseModel, Field


class AuditLogResponseDTO(BaseModel):
    """DTO de respuesta para visualización de un registro de auditoría."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    previous_state: dict[str, Any] | None
    new_state: dict[str, Any] | None
    created_at: datetime


class PaginatedAuditLogsResponseDTO(BaseModel):
    """DTO paginado de registros de auditoría."""

    total: int = Field(description="Total de registros existentes para el tenant")
    skip: int = Field(description="Desplazamiento aplicado")
    limit: int = Field(description="Límite por página")
    items: list[AuditLogResponseDTO]

