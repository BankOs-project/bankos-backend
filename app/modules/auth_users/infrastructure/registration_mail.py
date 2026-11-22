"""Entrega verificaciones por el canal de correo configurado."""

import logging

import httpx

from app.core.config import settings
from app.core.exceptions import BankOSException

logger = logging.getLogger("bankos.registration")


async def send_registration_code(email: str, code: str, *, source: bool = False) -> None:
    purpose = "autorizar una vinculación desde tu banco actual" if source else "verificar tu correo de registro"
    await _send_code(email, code, purpose, "REGISTRATION_UNAVAILABLE")


async def send_profile_email_code(email: str, code: str) -> None:
    await _send_code(email, code, "confirmar este nuevo correo de acceso", "PROFILE_EMAIL_UNAVAILABLE")


async def _send_code(email: str, code: str, purpose: str, unavailable_code: str) -> None:
    api_key = settings.RESEND_API_KEY.get_secret_value() if settings.RESEND_API_KEY else None
    if not api_key or not settings.REGISTRATION_EMAIL_FROM:
        raise BankOSException("El envío de correo no está disponible", unavailable_code, 503)

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": settings.REGISTRATION_EMAIL_FROM,
                    "to": [email],
                    "subject": "Verificación de BankOS",
                    "text": (
                        f"Usa este código para {purpose} en BankOS. Vence en 20 minutos.\n\n"
                        f"{code}\n\nSi no solicitaste el código, ignora este correo."
                    ),
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Falló la entrega de una verificación de correo")
        raise BankOSException("El envío de correo no está disponible", unavailable_code, 503) from None
