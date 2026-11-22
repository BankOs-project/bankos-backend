from datetime import datetime
from typing import Annotated, Literal
import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints, field_validator

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


class UpdateProfileDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Annotated[str, StringConstraints(min_length=3, max_length=150)] | None = None
    document_type: Literal["CC", "CE"] | None = None
    document_number: Annotated[str, StringConstraints(pattern=r"^[0-9]{3,12}$")] | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+[1-9][0-9]{7,14}$")] | None = None
    address: Annotated[str, StringConstraints(max_length=250)] | None = None

    @field_validator("document_type", "document_number", "phone", "address", mode="before")
    @classmethod
    def normalize_optional(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        if value is None:
            raise ValueError("El nombre completo es obligatorio")
        return value.strip() if isinstance(value, str) else value

    @field_validator("full_name", "address")
    @classmethod
    def reject_control_characters(cls, value):
        if value and not value.isprintable():
            raise ValueError("Usa caracteres imprimibles")
        return value


class StartProfileEmailChangeDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_email: EmailStr = Field(max_length=255)
    current_password: SecretStr = Field(min_length=1, max_length=1024)


class VerifyProfileEmailChangeDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    email_code: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email_code")
    @classmethod
    def validate_code(cls, value: SecretStr):
        if not value.get_secret_value().isascii():
            raise ValueError("El código contiene caracteres no válidos")
        return value

