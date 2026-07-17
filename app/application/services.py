import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.commands import CreatePaymentCommand
from app.application.exceptions import (
    IdempotencyConflictError,
    PaymentNotFoundError,
    PersistenceConflictError,
)
from app.application.fingerprint import create_request_fingerprint, safe_idempotency_key_hash
from app.application.gateway import GatewayDecision
from app.application.outbox import OutboxEventDraft
from app.application.ports import Clock, EventPublisher, PaymentGateway, UnitOfWork, WebhookClient
from app.application.webhooks import build_webhook_delivery_id, build_webhook_payload
from app.domain.payment import Payment

PAYMENT_CREATED_EVENT_TYPE = "payments.new"
PAYMENT_AGGREGATE_TYPE = "payment"
logger = logging.getLogger(__name__)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class CreatePaymentService:
    uow_factory: Callable[[], UnitOfWork]

    async def __call__(self, command: CreatePaymentCommand) -> Payment:
        fingerprint = create_request_fingerprint(command)
        key_hash = safe_idempotency_key_hash(command.idempotency_key)
        logger.info(
            "payment_create_requested",
            extra={"event": "payment_create_requested", "idempotency_key_hash": key_hash},
        )
        try:
            return await self._create(command, fingerprint)
        except PersistenceConflictError as exc:
            logger.info(
                "payment_idempotency_race",
                extra={"event": "payment_idempotency_race", "idempotency_key_hash": key_hash},
            )
            return await self._resolve_idempotency_race(
                command.idempotency_key,
                fingerprint,
                exc,
            )

    async def _create(
        self,
        command: CreatePaymentCommand,
        fingerprint: str,
    ) -> Payment:
        async with self.uow_factory() as uow:
            existing = await uow.payments.get_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                payment = self._resolve_existing(existing, fingerprint)
                await uow.commit()
                logger.info(
                    "payment_idempotency_replay",
                    extra={
                        "event": "payment_idempotency_replay",
                        "payment_id": str(payment.id),
                        "idempotency_key_hash": safe_idempotency_key_hash(command.idempotency_key),
                    },
                )
                return payment

            payment = Payment.create(
                amount=command.amount,
                currency=command.currency,
                description=command.description,
                metadata=command.metadata,
                webhook_url=command.webhook_url,
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )
            payment = await uow.payments.add(payment)
            if payment.id is None:
                raise RuntimeError("Payment repository returned payment without ID")

            outbox_event = await uow.outbox.add(
                OutboxEventDraft(
                    event_type=PAYMENT_CREATED_EVENT_TYPE,
                    aggregate_type=PAYMENT_AGGREGATE_TYPE,
                    aggregate_id=payment.id,
                    payload={"payment_id": str(payment.id)},
                )
            )
            await uow.commit()
            logger.info(
                "payment_created",
                extra={
                    "event": "payment_created",
                    "payment_id": str(payment.id),
                    "status": payment.status.value,
                    "idempotency_key_hash": safe_idempotency_key_hash(command.idempotency_key),
                },
            )
            logger.info(
                "outbox_event_created",
                extra={
                    "event": "outbox_event_created",
                    "payment_id": str(payment.id),
                    "outbox_id": str(outbox_event.id),
                    "event_type": outbox_event.event_type,
                },
            )
            return payment

    async def _resolve_idempotency_race(
        self,
        idempotency_key: str,
        fingerprint: str,
        original_error: PersistenceConflictError,
    ) -> Payment:
        async with self.uow_factory() as uow:
            existing = await uow.payments.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise original_error
            payment = self._resolve_existing(existing, fingerprint)
            await uow.commit()
            logger.info(
                "payment_idempotency_replay",
                extra={
                    "event": "payment_idempotency_replay",
                    "payment_id": str(payment.id),
                    "idempotency_key_hash": safe_idempotency_key_hash(idempotency_key),
                },
            )
            return payment

    @staticmethod
    def _resolve_existing(payment: Payment, fingerprint: str) -> Payment:
        if payment.request_fingerprint != fingerprint:
            logger.warning(
                "payment_idempotency_conflict",
                extra={
                    "event": "payment_idempotency_conflict",
                    "payment_id": str(payment.id),
                    "idempotency_key_hash": safe_idempotency_key_hash(payment.idempotency_key),
                },
            )
            raise IdempotencyConflictError("Idempotency key was reused with another payload")
        return payment


@dataclass(slots=True)
class GetPaymentService:
    uow_factory: Callable[[], UnitOfWork]

    async def __call__(self, payment_id: UUID) -> Payment:
        async with self.uow_factory() as uow:
            payment = await uow.payments.get_by_id(payment_id)
            if payment is None:
                raise PaymentNotFoundError(f"Payment {payment_id} was not found")
            return payment


@dataclass(slots=True)
class PublishOutboxBatchService:
    uow_factory: Callable[[], UnitOfWork]
    publisher: EventPublisher

    async def __call__(self, limit: int) -> int:
        published = 0
        async with self.uow_factory() as uow:
            events = await uow.outbox.get_pending_batch(limit)
            logger.info(
                "outbox_polling_finished",
                extra={"event": "outbox_polling_finished", "batch_size": len(events)},
            )
            for event in events:
                try:
                    logger.info(
                        "outbox_publish_started",
                        extra={
                            "event": "outbox_publish_started",
                            "outbox_id": str(event.id),
                            "payment_id": str(event.aggregate_id),
                            "message_id": str(event.id),
                        },
                    )
                    await self.publisher.publish(event)
                except Exception as exc:
                    await uow.outbox.record_publish_failure(event.id, str(exc))
                    logger.warning(
                        "outbox_publish_failed",
                        extra={
                            "event": "outbox_publish_failed",
                            "outbox_id": str(event.id),
                            "payment_id": str(event.aggregate_id),
                            "message_id": str(event.id),
                            "reason": str(exc),
                        },
                    )
                    continue

                await uow.outbox.mark_published(event.id)
                published += 1
                logger.info(
                    "outbox_event_published",
                    extra={
                        "event": "outbox_event_published",
                        "outbox_id": str(event.id),
                        "payment_id": str(event.aggregate_id),
                        "message_id": str(event.id),
                    },
                )
            await uow.commit()
        return published


@dataclass(slots=True)
class DeliverPaymentWebhookService:
    uow_factory: Callable[[], UnitOfWork]
    webhook_client: WebhookClient
    clock: Clock = SystemClock()

    async def __call__(self, payment_id: UUID) -> bool:
        async with self.uow_factory() as uow:
            payment = await uow.payments.get_by_id(payment_id)
            if payment is None:
                raise PaymentNotFoundError(f"Payment {payment_id} was not found")
            if payment.webhook_delivered_at is not None:
                await uow.commit()
                return False
            if payment.id is None:
                raise RuntimeError("Payment must have an ID before webhook delivery")

            payload = build_webhook_payload(payment)
            delivery_id = build_webhook_delivery_id(payment.id)
            try:
                logger.info(
                    "webhook_delivery_started",
                    extra={
                        "event": "webhook_delivery_started",
                        "payment_id": str(payment.id),
                        "status": payment.status.value,
                        "webhook_attempt": payment.webhook_attempts + 1,
                    },
                )
                await self.webhook_client.deliver(
                    url=payment.webhook_url,
                    payload=payload.as_dict(),
                    headers={"X-Webhook-Id": delivery_id},
                )
            except Exception as exc:
                payment.webhook_attempts += 1
                payment.last_webhook_error = str(exc)
                await uow.payments.save(payment)
                await uow.commit()
                logger.warning(
                    "webhook_delivery_failed",
                    extra={
                        "event": "webhook_delivery_failed",
                        "payment_id": str(payment.id),
                        "status": payment.status.value,
                        "webhook_attempt": payment.webhook_attempts,
                        "reason": str(exc),
                    },
                )
                raise

            payment.webhook_delivered_at = self.clock.now()
            payment.last_webhook_error = None
            await uow.payments.save(payment)
            await uow.commit()
            logger.info(
                "webhook_delivered",
                extra={
                    "event": "webhook_delivered",
                    "payment_id": str(payment.id),
                    "status": payment.status.value,
                },
            )
            return True


@dataclass(slots=True)
class ProcessPaymentMessageService:
    uow_factory: Callable[[], UnitOfWork]
    gateway: PaymentGateway
    webhook_delivery: DeliverPaymentWebhookService
    clock: Clock = SystemClock()

    async def __call__(self, payment_id: UUID) -> Payment:
        async with self.uow_factory() as uow:
            payment = await uow.payments.get_by_id_for_update(payment_id)
            if payment is None:
                raise PaymentNotFoundError(f"Payment {payment_id} was not found")

            should_deliver_webhook = payment.webhook_delivered_at is None
            if payment.is_final:
                logger.info(
                    "payment_processing_noop",
                    extra={
                        "event": "payment_processing_noop",
                        "payment_id": str(payment.id),
                        "status": payment.status.value,
                    },
                )
                await uow.commit()
            else:
                logger.info(
                    "payment_processing_started",
                    extra={"event": "payment_processing_started", "payment_id": str(payment.id)},
                )
                result = await self.gateway.process(payment)
                processed_at = self.clock.now()
                if result.decision == GatewayDecision.SUCCEEDED:
                    payment.succeed(processed_at)
                    log_event = "payment_succeeded"
                else:
                    payment.fail(processed_at)
                    log_event = "payment_failed"
                payment = await uow.payments.save(payment)
                await uow.commit()
                logger.info(
                    log_event,
                    extra={
                        "event": log_event,
                        "payment_id": str(payment.id),
                        "status": payment.status.value,
                    },
                )

        if should_deliver_webhook:
            await self.webhook_delivery(payment.id)
        return payment
