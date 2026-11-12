from datetime import datetime
import uuid
from pydantic import BaseModel, EmailStr, Field

from app.modules.auth_users.domain.entities import UserRole


class LoginRequestDTO(BaseModel):
    """Esquema de solicitud de autenticación multidominio/tenant."""

    tenant_slug: str = Field(description="Slug identificador de la institución financiera")
    email: EmailStr = Field(description="Correo electrónico del usuario")
    password: str = Field(min_length=6, description="Contraseña en texto plano")


class TokenResponseDTO(BaseModel):
    """Esquema de respuesta con el JWT generado tras autenticación exitosa."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Tiempo de vida en segundos")
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: UserRole


class CreateUserDTO(BaseModel):
    """Esquema para la creación de usuarios por parte de un administrador."""

    email: EmailStr = Field(description="Correo electrónico del nuevo usuario")
    password: str = Field(min_length=8, description="Contraseña inicial")
    full_name: str = Field(min_length=3, max_length=150, description="Nombre completo")
    role: UserRole = Field(description="Rol: 'administrador' o 'cliente'")


class UserResponseDTO(BaseModel):
    """Esquema de respuesta seguro que no expone hashes de contraseña."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime

