import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.commands import CreatePaymentCommand
from app.application.exceptions import (
    IdempotencyConflictError,
    PaymentNotFoundError,
    PermanentInfrastructureError,
    PersistenceConflictError,
    TemporaryInfrastructureError,
)
from app.application.gateway import GatewayDecision, GatewayResult
from app.application.outbox import OutboxEvent, OutboxEventDraft
from app.application.services import (
    CreatePaymentService,
    DeliverPaymentWebhookService,
    GetPaymentService,
    ProcessPaymentMessageService,
    PublishOutboxBatchService,
)
from app.application.webhooks import classify_webhook_status
from app.domain import Currency, Payment, PaymentStatus


@dataclass(slots=True)
class Store:
    payments: dict[UUID, Payment] = field(default_factory=dict)
    outbox: dict[UUID, OutboxEvent] = field(default_factory=dict)
    conflict_once: bool = False
    fail_payment_add: bool = False
    fail_outbox_add: bool = False
    fail_commit: bool = False


class FakePaymentRepository:
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self.uow = uow

    async def add(self, payment: Payment) -> Payment:
        if self.uow.store.fail_payment_add:
            raise PersistenceConflictError("payment insert failed")
        if self.uow.store.conflict_once:
            self.uow.store.conflict_once = False
            raise PersistenceConflictError("simulated unique conflict")
        payment = deepcopy(payment)
        payment.id = payment.id or uuid4()
        payment.created_at = payment.created_at or self.uow.now
        self.uow.payments_data[payment.id] = payment
        return deepcopy(payment)

    async def save(self, payment: Payment) -> Payment:
        if payment.id is None:
            raise ValueError("Payment must have ID")
        self.uow.payments_data[payment.id] = deepcopy(payment)
        return deepcopy(payment)

    async def get_by_id(self, payment_id: UUID) -> Payment | None:
        payment = self.uow.payments_data.get(payment_id)
        return deepcopy(payment) if payment is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        for payment in self.uow.payments_data.values():
            if payment.idempotency_key == idempotency_key:
                return deepcopy(payment)
        return None

    async def get_by_id_for_update(self, payment_id: UUID) -> Payment | None:
        return await self.get_by_id(payment_id)


class FakeOutboxRepository:
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self.uow = uow

    async def add(self, event: OutboxEventDraft) -> OutboxEvent:
        if self.uow.store.fail_outbox_add:
            raise PersistenceConflictError("outbox insert failed")
        outbox_event = OutboxEvent(
            id=uuid4(),
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            payload=event.payload,
            status="pending",
            attempts=0,
            created_at=self.uow.now,
            published_at=None,
            last_error=None,
        )
        self.uow.outbox_data[outbox_event.id] = outbox_event
        return deepcopy(outbox_event)

    async def get_pending_batch(self, limit: int) -> list[OutboxEvent]:
        return [
            deepcopy(event)
            for event in self.uow.outbox_data.values()
            if event.status == "pending"
        ][:limit]

    async def mark_published(self, event_id: UUID) -> None:
        event = self.uow.outbox_data[event_id]
        event.status = "published"
        event.published_at = self.uow.now
        event.last_error = None

    async def record_publish_failure(self, event_id: UUID, error: str) -> None:
        event = self.uow.outbox_data[event_id]
        event.attempts += 1
        event.last_error = error


class FakeUnitOfWork:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.now = datetime.now(UTC)
        self.committed = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.payments_data = deepcopy(self.store.payments)
        self.outbox_data = deepcopy(self.store.outbox)
        self.payments = FakePaymentRepository(self)
        self.outbox = FakeOutboxRepository(self)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: object | None) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        if self.store.fail_commit:
            raise PersistenceConflictError("commit failed")
        for payment in self.payments_data.values():
            for existing_id, existing in self.store.payments.items():
                if (
                    existing_id != payment.id
                    and existing.idempotency_key == payment.idempotency_key
                ):
                    raise PersistenceConflictError("duplicate idempotency key")
        self.store.payments = deepcopy(self.payments_data)
        self.store.outbox = deepcopy(self.outbox_data)
        self.committed = True

    async def rollback(self) -> None:
        self.committed = False


class FakeGateway:
    def __init__(
        self,
        decision: GatewayDecision = GatewayDecision.SUCCEEDED,
        error: Exception | None = None,
    ) -> None:
        self.decision = decision
        self.error = error
        self.calls = 0

    async def process(self, payment: Payment) -> GatewayResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return GatewayResult(self.decision)


class FakeWebhookClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def deliver(self, *, url: str, payload: dict[str, object], headers: dict[str, str]) -> None:
        self.calls.append({"url": url, "payload": payload, "headers": headers})


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[OutboxEvent] = []

    async def publish(self, event: OutboxEvent) -> None:
        if self.fail:
            raise RuntimeError("broker unavailable")
        self.published.append(event)


def make_command(idempotency_key: str = "idem-key", amount: Decimal = Decimal("10.50")) -> CreatePaymentCommand:
    return CreatePaymentCommand(
        amount=amount,
        currency=Currency.RUB,
        description="Order 42",
        metadata={"order_id": "42"},
        webhook_url="https://example.com/webhooks/payments",
        idempotency_key=idempotency_key,
    )


async def create_payment(store: Store, command: CreatePaymentCommand | None = None) -> Payment:
    service = CreatePaymentService(lambda: FakeUnitOfWork(store))
    return await service(command or make_command())


async def test_create_payment_commits_payment_and_outbox_atomically() -> None:
    store = Store()

    payment = await create_payment(store)

    assert payment.status == PaymentStatus.PENDING
    assert len(store.payments) == 1
    assert len(store.outbox) == 1
    event = next(iter(store.outbox.values()))
    assert event.event_type == "payments.new"
    assert event.aggregate_id == payment.id


async def test_create_payment_rollback_leaves_no_partial_state() -> None:
    store = Store()
    service = CreatePaymentService(lambda: FakeUnitOfWork(store))

    with pytest.raises(PersistenceConflictError):
        await service._resolve_idempotency_race("missing-key", "fingerprint", PersistenceConflictError("boom"))

    assert store.payments == {}
    assert store.outbox == {}


@pytest.mark.parametrize(
    "flag",
    ["fail_payment_add", "fail_outbox_add", "fail_commit"],
)
async def test_create_payment_failure_rolls_back_payment_and_outbox(flag: str) -> None:
    store = Store()
    setattr(store, flag, True)
    service = CreatePaymentService(lambda: FakeUnitOfWork(store))

    with pytest.raises(PersistenceConflictError):
        await service(make_command())

    assert store.payments == {}
    assert store.outbox == {}


async def test_same_idempotency_key_and_same_payload_replays_existing_payment() -> None:
    store = Store()

    first = await create_payment(store)
    second = await create_payment(store)

    assert second.id == first.id
    assert len(store.payments) == 1
    assert len(store.outbox) == 1


async def test_same_idempotency_key_with_different_payload_conflicts() -> None:
    store = Store()
    await create_payment(store, make_command(amount=Decimal("10.50")))

    with pytest.raises(IdempotencyConflictError):
        await create_payment(store, make_command(amount=Decimal("11.00")))

    assert len(store.payments) == 1
    assert len(store.outbox) == 1


async def test_idempotency_race_is_resolved_by_reading_existing_payment() -> None:
    store = Store()
    existing = await create_payment(store)
    store.conflict_once = True

    replay = await create_payment(store)

    assert replay.id == existing.id
    assert len(store.payments) == 1
    assert len(store.outbox) == 1


async def test_concurrent_duplicate_idempotency_key_creates_single_payment_and_outbox() -> None:
    store = Store()

    first, second = await asyncio.gather(create_payment(store), create_payment(store))

    assert first.id == second.id
    assert len(store.payments) == 1
    assert len(store.outbox) == 1


async def test_get_payment_success_and_not_found() -> None:
    store = Store()
    payment = await create_payment(store)
    service = GetPaymentService(lambda: FakeUnitOfWork(store))

    assert (await service(payment.id)).id == payment.id
    with pytest.raises(PaymentNotFoundError):
        await service(uuid4())


async def test_process_pending_payment_success_calls_gateway_once_and_delivers_webhook() -> None:
    store = Store()
    payment = await create_payment(store)
    gateway = FakeGateway(GatewayDecision.SUCCEEDED)
    webhook_client = FakeWebhookClient()
    webhook_service = DeliverPaymentWebhookService(lambda: FakeUnitOfWork(store), webhook_client)
    service = ProcessPaymentMessageService(lambda: FakeUnitOfWork(store), gateway, webhook_service)

    processed = await service(payment.id)

    assert processed.status == PaymentStatus.SUCCEEDED
    assert processed.processed_at is not None
    assert gateway.calls == 1
    assert len(webhook_client.calls) == 1
    assert webhook_client.calls[0]["payload"]["payment_id"] == str(payment.id)
    assert webhook_client.calls[0]["payload"]["status"] == PaymentStatus.SUCCEEDED.value
    assert webhook_client.calls[0]["payload"]["processed_at"] is not None
    assert webhook_client.calls[0]["headers"]["X-Webhook-Id"] == f"payment:{payment.id}:status-webhook"
    assert store.payments[payment.id].webhook_delivered_at is not None


async def test_gateway_business_failure_marks_payment_failed_and_delivers_webhook() -> None:
    store = Store()
    payment = await create_payment(store)
    gateway = FakeGateway(GatewayDecision.FAILED)
    webhook_client = FakeWebhookClient()
    webhook_service = DeliverPaymentWebhookService(lambda: FakeUnitOfWork(store), webhook_client)
    service = ProcessPaymentMessageService(lambda: FakeUnitOfWork(store), gateway, webhook_service)

    processed = await service(payment.id)

    assert processed.status == PaymentStatus.FAILED
    assert processed.processed_at is not None
    assert gateway.calls == 1
    assert len(webhook_client.calls) == 1
    assert webhook_client.calls[0]["payload"]["status"] == PaymentStatus.FAILED.value


async def test_gateway_technical_error_is_propagated_without_final_status() -> None:
    store = Store()
    payment = await create_payment(store)
    gateway = FakeGateway(error=TemporaryInfrastructureError("gateway timeout"))
    webhook_client = FakeWebhookClient()
    webhook_service = DeliverPaymentWebhookService(lambda: FakeUnitOfWork(store), webhook_client)
    service = ProcessPaymentMessageService(lambda: FakeUnitOfWork(store), gateway, webhook_service)

    with pytest.raises(TemporaryInfrastructureError):
        await service(payment.id)

    assert store.payments[payment.id].status == PaymentStatus.PENDING
    assert webhook_client.calls == []


async def test_replayed_message_for_final_payment_does_not_call_gateway_again() -> None:
    store = Store()
    payment = await create_payment(store)
    gateway = FakeGateway()
    webhook_client = FakeWebhookClient()
    webhook_service = DeliverPaymentWebhookService(lambda: FakeUnitOfWork(store), webhook_client)
    service = ProcessPaymentMessageService(lambda: FakeUnitOfWork(store), gateway, webhook_service)

    await service(payment.id)
    await service(payment.id)

    assert gateway.calls == 1
    assert len(webhook_client.calls) == 1


async def test_replayed_final_payment_with_failed_webhook_retries_only_webhook() -> None:
    store = Store()
    payment = await create_payment(store)
    stored = store.payments[payment.id]
    stored.succeed(datetime.now(UTC))
    stored.webhook_attempts = 1
    stored.last_webhook_error = "temporary webhook failure"
    gateway = FakeGateway()
    webhook_client = FakeWebhookClient()
    webhook_service = DeliverPaymentWebhookService(lambda: FakeUnitOfWork(store), webhook_client)
    service = ProcessPaymentMessageService(lambda: FakeUnitOfWork(store), gateway, webhook_service)

    processed = await service(payment.id)

    assert processed.status == PaymentStatus.SUCCEEDED
    assert gateway.calls == 0
    assert len(webhook_client.calls) == 1
    assert webhook_client.calls[0]["headers"]["X-Webhook-Id"] == f"payment:{payment.id}:status-webhook"
    assert store.payments[payment.id].webhook_delivered_at is not None
    assert store.payments[payment.id].last_webhook_error is None


async def test_delivered_webhook_is_not_sent_again() -> None:
    store = Store()
    payment = await create_payment(store)
    stored = store.payments[payment.id]
    stored.succeed(datetime.now(UTC))
    stored.webhook_delivered_at = datetime.now(UTC)
    webhook_client = FakeWebhookClient()
    service = DeliverPaymentWebhookService(lambda: FakeUnitOfWork(store), webhook_client)

    delivered = await service(payment.id)

    assert delivered is False
    assert webhook_client.calls == []


async def test_publish_failure_does_not_mark_outbox_published() -> None:
    store = Store()
    await create_payment(store)
    publisher = FakePublisher(fail=True)
    service = PublishOutboxBatchService(lambda: FakeUnitOfWork(store), publisher)

    published = await service(100)

    event = next(iter(store.outbox.values()))
    assert published == 0
    assert event.status == "pending"
    assert event.attempts == 1
    assert event.last_error == "broker unavailable"


async def test_outbox_success_marks_event_published() -> None:
    store = Store()
    await create_payment(store)
    publisher = FakePublisher()
    service = PublishOutboxBatchService(lambda: FakeUnitOfWork(store), publisher)

    published = await service(100)

    event = next(iter(store.outbox.values()))
    assert published == 1
    assert event.status == "published"
    assert event.published_at is not None
    assert len(publisher.published) == 1


async def test_outbox_logs_stable_message_id_after_publish_confirm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = Store()
    await create_payment(store)
    publisher = FakePublisher()
    service = PublishOutboxBatchService(lambda: FakeUnitOfWork(store), publisher)
    caplog.set_level("INFO")

    published = await service(100)

    event = next(iter(store.outbox.values()))
    publish_started = [
        record
        for record in caplog.records
        if record.__dict__.get("event") == "outbox_publish_started"
    ]
    event_published = [
        record
        for record in caplog.records
        if record.__dict__.get("event") == "outbox_event_published"
    ]
    assert published == 1
    assert publish_started[0].__dict__["message_id"] == str(event.id)
    assert event_published[0].__dict__["message_id"] == str(event.id)


async def test_relay_after_publish_failure_can_publish_pending_event() -> None:
    store = Store()
    await create_payment(store)

    failed_publish = await PublishOutboxBatchService(
        lambda: FakeUnitOfWork(store),
        FakePublisher(fail=True),
    )(100)
    successful_publisher = FakePublisher()
    successful_publish = await PublishOutboxBatchService(
        lambda: FakeUnitOfWork(store),
        successful_publisher,
    )(100)

    event = next(iter(store.outbox.values()))
    assert failed_publish == 0
    assert successful_publish == 1
    assert event.status == "published"
    assert event.attempts == 1


@pytest.mark.parametrize("status_code", [200, 201, 204])
def test_webhook_status_classifier_accepts_2xx(status_code: int) -> None:
    classify_webhook_status(status_code)


@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
def test_webhook_status_classifier_marks_retryable_errors(status_code: int) -> None:
    with pytest.raises(TemporaryInfrastructureError):
        classify_webhook_status(status_code)


@pytest.mark.parametrize("status_code", [400, 401, 404, 422])
def test_webhook_status_classifier_marks_permanent_errors(status_code: int) -> None:
    with pytest.raises(PermanentInfrastructureError):
        classify_webhook_status(status_code)
