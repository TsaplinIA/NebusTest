from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import PersistenceConflictError
from app.application.outbox import OutboxEvent, OutboxEventDraft
from app.domain.payment import Payment
from app.infrastructure.db.models import (
    OutboxModel,
    OutboxStatus,
    PaymentModel,
    model_to_payment,
    payment_to_model,
)


class SqlAlchemyPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payment: Payment) -> Payment:
        model = payment_to_model(payment)
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except IntegrityError as exc:
            raise PersistenceConflictError(str(exc)) from exc
        return model_to_payment(model)

    async def save(self, payment: Payment) -> Payment:
        if payment.id is None:
            raise ValueError("Cannot save payment without ID")
        await self._session.execute(
            update(PaymentModel)
            .where(PaymentModel.id == payment.id)
            .values(
                amount=payment.amount,
                currency=payment.currency.value,
                description=payment.description,
                metadata_json=payment.metadata,
                status=payment.status.value,
                idempotency_key=payment.idempotency_key,
                request_fingerprint=payment.request_fingerprint,
                webhook_url=payment.webhook_url,
                processed_at=payment.processed_at,
                webhook_delivered_at=payment.webhook_delivered_at,
                webhook_attempts=payment.webhook_attempts,
                last_webhook_error=payment.last_webhook_error,
            )
        )
        await self._session.flush()
        refreshed = await self.get_by_id(payment.id)
        if refreshed is None:
            raise ValueError("Payment disappeared during save")
        return refreshed

    async def get_by_id(self, payment_id: UUID) -> Payment | None:
        model = await self._session.get(PaymentModel, payment_id)
        return model_to_payment(model) if model is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        result = await self._session.execute(
            select(PaymentModel).where(PaymentModel.idempotency_key == idempotency_key)
        )
        model = result.scalar_one_or_none()
        return model_to_payment(model) if model is not None else None

    async def get_by_id_for_update(self, payment_id: UUID) -> Payment | None:
        result = await self._session.execute(
            select(PaymentModel)
            .where(PaymentModel.id == payment_id)
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        return model_to_payment(model) if model is not None else None


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: OutboxEventDraft) -> OutboxEvent:
        model = OutboxModel(
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            payload=event.payload,
            status=OutboxStatus.PENDING,
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except IntegrityError as exc:
            raise PersistenceConflictError(str(exc)) from exc
        return self._to_event(model)

    async def get_pending_batch(self, limit: int) -> Sequence[OutboxEvent]:
        result = await self._session.execute(self.pending_batch_statement(limit))
        return [self._to_event(model) for model in result.scalars()]

    async def mark_published(self, event_id: UUID) -> None:
        await self._session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == event_id)
            .values(
                status=OutboxStatus.PUBLISHED,
                published_at=datetime.now(UTC),
                last_error=None,
            )
        )

    async def record_publish_failure(self, event_id: UUID, error: str) -> None:
        await self._session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == event_id)
            .values(
                attempts=OutboxModel.attempts + 1,
                last_error=error,
            )
        )

    @staticmethod
    def pending_batch_statement(limit: int) -> Select[tuple[OutboxModel]]:
        return (
            select(OutboxModel)
            .where(OutboxModel.status == OutboxStatus.PENDING)
            .order_by(OutboxModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    @staticmethod
    def _to_event(model: OutboxModel) -> OutboxEvent:
        return OutboxEvent(
            id=model.id,
            event_type=model.event_type,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            payload=model.payload,
            status=model.status,
            attempts=model.attempts,
            created_at=model.created_at,
            published_at=model.published_at,
            last_error=model.last_error,
        )
