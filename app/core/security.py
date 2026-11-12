from datetime import datetime, timedelta, timezone
from typing import Any, Literal
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from pwdlib import PasswordHash

from app.core.config import settings

# Instancia recomendada de PasswordHash (utiliza Argon2id y soporte de actualización desde Bcrypt)
password_hash = PasswordHash.recommended()

# Esquema de seguridad Bearer Token
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login",
    auto_error=True,
)

# Definición de roles autorizados en BankOS
RoleType = Literal["administrador", "cliente"]


class TokenPayload(BaseModel):
    """Estructura estricta de claims contenidos en el token de autenticación JWT."""

    sub: str = Field(description="Identificador principal del sujeto (usualmente user_id)")
    user_id: str = Field(description="UUID o identificador único del usuario dentro del tenant")
    tenant_id: str = Field(description="UUID o identificador único del tenant al que pertenece el usuario")
    role: RoleType = Field(description="Rol asignado al usuario ('administrador' o 'cliente')")
    exp: datetime = Field(description="Fecha y hora de expiración del token UTC")
    iat: datetime = Field(description="Fecha y hora de emisión del token UTC")


def get_password_hash(password: str) -> str:
    """Genera un hash criptográfico seguro a partir de una contraseña en texto plano."""
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si una contraseña en texto plano coincide con su hash almacenado."""
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(
    tenant_id: str,
    user_id: str,
    role: RoleType,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Genera un token de acceso JWT firmado con los claims requeridos por BankOS.
    
    Claims obligatorios incluidos:
    - sub: user_id
    - user_id: ID del usuario autenticado
    - tenant_id: ID del tenant para aislamiento estricto
    - role: Rol operativo ('administrador' o 'cliente')
    - exp: Expiración del token
    - iat: Emisión del token
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: dict[str, Any] = {
        "sub": str(user_id),
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "iat": now,
        "exp": expire,
    }

    if extra_claims:
        to_encode.update(extra_claims)

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return encoded_jwt


def decode_access_token(token: str) -> TokenPayload:
    """Decodifica y valida la firma y los claims obligatorios del token JWT.
    
    Lanza HTTPException 401 si el token es inválido, ha expirado o carece de claims obligatorios.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de autenticación inválidas o expiradas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return TokenPayload(**payload)
    except (jwt.PyJWTError, ValueError):
        raise credentials_exception


async def get_current_token_payload(
    token: str = Depends(oauth2_scheme),
) -> TokenPayload:
    """Inyector de dependencias FastAPI para obtener y validar el payload del token JWT actual."""
    return decode_access_token(token)


class RoleChecker:
    """Verificador de autorización RBAC basado en roles permitidos."""

    def __init__(self, allowed_roles: list[RoleType]) -> None:
        self.allowed_roles = allowed_roles

    def __call__(self, token_payload: TokenPayload = Depends(get_current_token_payload)) -> TokenPayload:
        if token_payload.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permisos insuficientes. Se requiere uno de los siguientes roles: {self.allowed_roles}",
            )
        return token_payload


require_admin = RoleChecker(["administrador"])
require_client = RoleChecker(["cliente"])
require_any_authenticated = RoleChecker(["administrador", "cliente"])

