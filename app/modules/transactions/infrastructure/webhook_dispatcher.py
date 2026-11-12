import logging
import httpx
from app.modules.transactions.domain.repositories import WebhookNotifierPort

logger = logging.getLogger("bankos.webhook")

WEBHOOK_TIMEOUT_SECONDS = 10


class HttpxWebhookDispatcher(WebhookNotifierPort):
    """Implementación de despacho asíncrono de webhooks usando httpx.AsyncClient."""

    async def dispatch(
        self,
        webhook_url: str,
        event_type: str,
        payload: dict,
        correlation_id: str,
    ) -> None:
        """Envía una notificación HTTP POST al endpoint configurado por el tenant.

        Los errores de red se registran en logs pero NO propagan excepciones,
        garantizando que un fallo en el webhook nunca revierta la transacción bancaria.
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "X-Correlation-ID": correlation_id,
                "X-BankOS-Event": event_type,
                "User-Agent": "BankOS-Webhook/1.0",
            }
            body = {
                "event": event_type,
                "correlation_id": correlation_id,
                "data": payload,
            }
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
                response = await client.post(webhook_url, json=body, headers=headers)
                logger.info(
                    "Webhook '%s' despachado a %s — HTTP %s",
                    event_type,
                    webhook_url,
                    response.status_code,
                )
        except httpx.TimeoutException:
            logger.warning(
                "Timeout al despachar webhook '%s' a %s (timeout=%ss)",
                event_type,
                webhook_url,
                WEBHOOK_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            logger.error(
                "Error de red al despachar webhook '%s' a %s: %s",
                event_type,
                webhook_url,
                exc,
            )
        except Exception as exc:
            logger.exception(
                "Error inesperado en el dispatcher de webhook '%s' a %s: %s",
                event_type,
                webhook_url,
                exc,
            )

