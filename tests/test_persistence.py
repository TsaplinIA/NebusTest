import os
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.exceptions import PersistenceConflictError
from app.application.outbox import OutboxEventDraft
from app.domain import Currency, Payment
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import OutboxModel, PaymentModel
from app.infrastructure.db.repositories import SqlAlchemyOutboxRepository
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL integration test skipped")

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def make_payment(idempotency_key: str = "idem-key") -> Payment:
    return Payment.create(
        amount=Decimal("1250.50"),
        currency=Currency.RUB,
        description="Order 42",
        metadata={"order_id": "42"},
        webhook_url="https://example.com/webhooks/payments",
        idempotency_key=idempotency_key,
        request_fingerprint="fingerprint",
    )


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[PaymentModel] | type[OutboxModel],
) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_payment_and_outbox_can_be_committed_in_one_uow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        payment = await uow.payments.add(make_payment())
        event = await uow.outbox.add(
            OutboxEventDraft(
                event_type="payments.new",
                aggregate_type="payment",
                aggregate_id=payment.id,
                payload={"payment_id": str(payment.id)},
            )
        )
        await uow.commit()

    assert isinstance(payment.id, UUID)
    assert isinstance(event.id, UUID)
    assert await count_rows(session_factory, PaymentModel) == 1
    assert await count_rows(session_factory, OutboxModel) == 1


async def test_rollback_does_not_leave_payment_or_outbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        payment = await uow.payments.add(make_payment())
        await uow.outbox.add(
            OutboxEventDraft(
                event_type="payments.new",
                aggregate_type="payment",
                aggregate_id=payment.id,
                payload={"payment_id": str(payment.id)},
            )
        )
        await uow.rollback()

    assert await count_rows(session_factory, PaymentModel) == 0
    assert await count_rows(session_factory, OutboxModel) == 0


async def test_idempotency_key_is_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.payments.add(make_payment("same-key"))
        await uow.commit()

    with pytest.raises(PersistenceConflictError):
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await uow.payments.add(make_payment("same-key"))
            await uow.commit()


def test_pending_outbox_batch_uses_postgres_skip_locked() -> None:
    statement = SqlAlchemyOutboxRepository.pending_batch_statement(100)

    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in compiled
    assert "outbox.status = 'pending'" in compiled
