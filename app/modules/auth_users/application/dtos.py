from datetime import datetime
import uuid
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

from app.modules.auth_users.domain.entities import UserRole


class LoginRequestDTO(BaseModel):
    """Esquema de solicitud de autenticación multidominio/tenant."""

    bank_id: str | None = Field(default=None, description="Identificador público devuelto por GET /banks")
    tenant_slug: str | None = Field(default=None, description="Slug anterior; alternativa temporal a bank_id")
    email: EmailStr = Field(description="Correo electrónico del usuario")
    password: str = Field(min_length=6, description="Contraseña en texto plano")


class StartRegistrationDTO(BaseModel):
    bank_id: uuid.UUID
    email: EmailStr
    source_password: SecretStr | None = Field(default=None, max_length=1024)


class VerifyRegistrationDTO(BaseModel):
    registration_id: uuid.UUID
    email_code: SecretStr = Field(min_length=1, max_length=128)
    source_email_code: SecretStr | None = Field(default=None, max_length=128)
    full_name: str = Field(min_length=3, max_length=150)
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("full_name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


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

