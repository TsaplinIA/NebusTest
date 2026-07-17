import logging
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from app.application.exceptions import TemporaryInfrastructureError
from app.application.ports import WebhookClient
from app.application.webhooks import classify_webhook_status

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HttpxWebhookClient(WebhookClient):
    client: httpx.AsyncClient

    async def deliver(
        self,
        *,
        url: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> None:
        try:
            response = await self.client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "webhook_http_failed",
                extra={"event": "webhook_http_failed", "reason": str(exc)},
            )
            raise TemporaryInfrastructureError(str(exc)) from exc

        logger.info(
            "webhook_http_response_received",
            extra={"event": "webhook_http_response_received", "status_code": response.status_code},
        )
        classify_webhook_status(response.status_code)
