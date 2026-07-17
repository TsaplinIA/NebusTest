from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.gateway import GatewayResult
from app.application.outbox import OutboxEvent, OutboxEventDraft
from app.domain.payment import Payment


class PaymentRepository(Protocol):
    async def add(self, payment: Payment) -> Payment: ...

    async def save(self, payment: Payment) -> Payment: ...

    async def get_by_id(self, payment_id: UUID) -> Payment | None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None: ...

    async def get_by_id_for_update(self, payment_id: UUID) -> Payment | None: ...


class OutboxRepository(Protocol):
    async def add(self, event: OutboxEventDraft) -> OutboxEvent: ...

    async def get_pending_batch(self, limit: int) -> Sequence[OutboxEvent]: ...

    async def mark_published(self, event_id: UUID) -> None: ...

    async def record_publish_failure(self, event_id: UUID, error: str) -> None: ...


class UnitOfWork(Protocol):
    payments: PaymentRepository
    outbox: OutboxRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, event: OutboxEvent) -> None: ...


class PaymentGateway(Protocol):
    async def process(self, payment: Payment) -> GatewayResult: ...


class WebhookClient(Protocol):
    async def deliver(
        self,
        *,
        url: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
