import logging
from typing import Any
from uuid import UUID

import httpx
from faststream import FastStream
from faststream.middlewares.acknowledgement.config import AckPolicy
from faststream.rabbit import RabbitMessage
from pydantic import BaseModel, ValidationError

from app.application.exceptions import (
    PaymentNotFoundError,
    PermanentInfrastructureError,
    TemporaryInfrastructureError,
)
from app.application.services import (
    DeliverPaymentWebhookService,
    ProcessPaymentMessageService,
)
from app.core.config import get_settings
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.http.gateway import MockPaymentGateway
from app.infrastructure.http.webhook import HttpxWebhookClient
from app.infrastructure.messaging.broker import create_broker
from app.infrastructure.messaging.retry import parse_attempt
from app.infrastructure.messaging.retry_router import RabbitRetryRouter
from app.infrastructure.messaging.topology import build_topology, declare_topology

logger = logging.getLogger(__name__)

settings = get_settings()
broker = create_broker(settings)
topology = build_topology(settings)
_http_client: httpx.AsyncClient | None = None


class PaymentMessage(BaseModel):
    payment_id: UUID


async def after_startup() -> None:
    global _http_client
    await declare_topology(broker, topology)
    _http_client = httpx.AsyncClient(timeout=settings.webhook_timeout_seconds)


async def on_shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def build_process_service() -> ProcessPaymentMessageService:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=settings.webhook_timeout_seconds)

    gateway = MockPaymentGateway(
        min_seconds=settings.payment_processing_min_seconds,
        max_seconds=settings.payment_processing_max_seconds,
        success_rate=settings.payment_success_rate,
    )
    webhook_delivery = DeliverPaymentWebhookService(
        uow_factory=SqlAlchemyUnitOfWork,
        webhook_client=HttpxWebhookClient(_http_client),
    )
    return ProcessPaymentMessageService(
        uow_factory=SqlAlchemyUnitOfWork,
        gateway=gateway,
        webhook_delivery=webhook_delivery,
    )


def extract_message_metadata(message: RabbitMessage) -> tuple[dict[str, object], str | None]:
    raw_message = getattr(message, "raw_message", None)
    headers = getattr(raw_message, "headers", None) or {}
    message_id = getattr(raw_message, "message_id", None)
    return dict(headers), message_id


def normalize_payload(payload: Any) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    return {"malformed_payload": repr(payload)}


@broker.subscriber(
    topology.payments_queue,
    topology.events_exchange,
    ack_policy=AckPolicy.MANUAL,
)
async def handle_payment_message(payload: dict[str, object], message: RabbitMessage) -> None:
    headers, message_id = extract_message_metadata(message)
    attempt = parse_attempt(headers)
    retry_router = RabbitRetryRouter(broker=broker, topology=topology, settings=settings)

    try:
        event = PaymentMessage.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "message_sent_to_dlq",
            extra={"message_id": message_id, "attempt": attempt, "reason": str(exc)},
        )
        await retry_router.send_to_dlq(
            payload=normalize_payload(payload),
            current_attempt=attempt,
            message_id=message_id,
            reason="malformed payment message",
        )
        await message.ack()
        return

    try:
        logger.info(
            "payment_message_received",
            extra={
                "payment_id": str(event.payment_id),
                "message_id": message_id,
                "attempt": attempt,
            },
        )
        await build_process_service()(event.payment_id)
    except (PermanentInfrastructureError, PaymentNotFoundError) as exc:
        logger.warning(
            "message_sent_to_dlq",
            extra={
                "payment_id": str(event.payment_id),
                "message_id": message_id,
                "attempt": attempt,
                "reason": str(exc),
            },
        )
        await retry_router.send_to_dlq(
            payload=payload,
            current_attempt=attempt,
            message_id=message_id,
            reason=str(exc),
        )
        await message.ack()
        return
    except TemporaryInfrastructureError as exc:
        route = await retry_router.retry_or_dlq(
            payload=payload,
            current_attempt=attempt,
            message_id=message_id,
            reason=str(exc),
        )
        logger.warning(
            "message_retry_scheduled" if route == "retry" else "message_sent_to_dlq",
            extra={
                "payment_id": str(event.payment_id),
                "message_id": message_id,
                "attempt": attempt,
                "route": route,
            },
        )
        await message.ack()
        return
    except Exception as exc:
        route = await retry_router.retry_or_dlq(
            payload=payload,
            current_attempt=attempt,
            message_id=message_id,
            reason=str(exc),
        )
        logger.exception(
            "payment_message_processing_failed",
            extra={
                "payment_id": str(event.payment_id),
                "message_id": message_id,
                "attempt": attempt,
                "route": route,
            },
        )
        await message.ack()
        return

    await message.ack()


app = FastStream(
    broker,
    after_startup=[after_startup],
    on_shutdown=[on_shutdown],
)
