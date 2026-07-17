from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.application.exceptions import (
    PermanentInfrastructureError,
    TemporaryInfrastructureError,
)
from app.domain.payment import Payment


@dataclass(frozen=True, slots=True)
class WebhookPayload:
    payment_id: UUID
    status: str
    processed_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "payment_id": str(self.payment_id),
            "status": self.status,
            "processed_at": self.processed_at.isoformat()
            if self.processed_at is not None
            else None,
        }


def build_webhook_delivery_id(payment_id: UUID) -> str:
    return f"payment:{payment_id}:status-webhook"


def build_webhook_payload(payment: Payment) -> WebhookPayload:
    if payment.id is None:
        raise ValueError("Payment must have an ID before webhook delivery")
    return WebhookPayload(
        payment_id=payment.id,
        status=payment.status.value,
        processed_at=payment.processed_at,
    )


def classify_webhook_status(status_code: int) -> None:
    if 200 <= status_code < 300:
        return
    if status_code in {408, 429} or status_code >= 500:
        raise TemporaryInfrastructureError(
            f"Webhook delivery failed with retryable HTTP {status_code}"
        )
    raise PermanentInfrastructureError(
        f"Webhook delivery failed with permanent HTTP {status_code}"
    )
