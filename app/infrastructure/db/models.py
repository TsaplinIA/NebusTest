from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.domain.enums import Currency, PaymentStatus
from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.domain.payment import Payment

json_type = JSON().with_variant(JSONB, "postgresql")


class PaymentModel(Base):
    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        json_type,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentStatus.PENDING.value,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    webhook_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    webhook_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_webhook_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_payments_status", "status"),
    )


class OutboxStatus:
    PENDING = "pending"
    PUBLISHED = "published"


class OutboxModel(Base):
    __tablename__ = "outbox"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OutboxStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "ix_outbox_pending_created_at",
            "status",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_outbox_aggregate_id", "aggregate_id"),
    )


def payment_to_model(payment: "PaymentModel | Payment") -> PaymentModel:
    from app.domain.payment import Payment

    if isinstance(payment, PaymentModel):
        return payment
    if not isinstance(payment, Payment):
        raise TypeError("payment must be a Payment domain entity")

    model = PaymentModel(
        amount=payment.amount,
        currency=Currency(payment.currency).value,
        description=payment.description,
        metadata_json=payment.metadata,
        status=PaymentStatus(payment.status).value,
        idempotency_key=payment.idempotency_key,
        request_fingerprint=payment.request_fingerprint,
        webhook_url=payment.webhook_url,
        processed_at=payment.processed_at,
        webhook_delivered_at=payment.webhook_delivered_at,
        webhook_attempts=payment.webhook_attempts,
        last_webhook_error=payment.last_webhook_error,
    )
    if payment.id is not None:
        model.id = payment.id
    if payment.created_at is not None:
        model.created_at = payment.created_at
    return model


def model_to_payment(model: PaymentModel) -> "Payment":
    from app.domain.payment import Payment

    return Payment(
        id=model.id,
        amount=model.amount,
        currency=Currency(model.currency),
        description=model.description,
        metadata=model.metadata_json,
        webhook_url=model.webhook_url,
        idempotency_key=model.idempotency_key,
        request_fingerprint=model.request_fingerprint,
        status=PaymentStatus(model.status),
        created_at=model.created_at,
        processed_at=model.processed_at,
        webhook_delivered_at=model.webhook_delivered_at,
        webhook_attempts=model.webhook_attempts,
        last_webhook_error=model.last_webhook_error,
    )
