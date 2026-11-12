from dataclasses import dataclass
import uuid
from fastapi import Depends

from app.core.exceptions import AuthorizationFailedException
from app.core.security import TokenPayload, get_current_token_payload
from app.modules.auth_users.domain.entities import UserRole


@dataclass
class AuthenticatedUser:
    """Información de identidad y alcance multi-tenant extraída de forma segura del JWT."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: UserRole
    sub: str


async def get_current_user(
    token_payload: TokenPayload = Depends(get_current_token_payload),
) -> AuthenticatedUser:
    """Extrae y valida los claims indispensables (user_id, tenant_id, role) del JWT."""
    try:
        user_uuid = uuid.UUID(token_payload.user_id)
        tenant_uuid = uuid.UUID(token_payload.tenant_id)
        role = UserRole(token_payload.role)
    except ValueError as exc:
        raise AuthorizationFailedException("Claims del token de sesión malformados o inválidos") from exc

    return AuthenticatedUser(
        user_id=user_uuid,
        tenant_id=tenant_uuid,
        role=role,
        sub=token_payload.sub,
    )


async def require_admin_role(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Garantiza que el usuario autenticado posea estrictamente el rol 'administrador'."""
    if current_user.role != UserRole.ADMIN:
        raise AuthorizationFailedException(
            message="Acceso restringido: Se requiere rol de 'administrador' para ejecutar esta operación"
        )
    return current_user

