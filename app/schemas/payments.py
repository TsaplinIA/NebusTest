from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import Currency, PaymentStatus
from app.domain.payment import Payment

Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class PaymentCreate(BaseModel):
    amount: Amount
    currency: Currency
    description: Annotated[str, Field(min_length=1, max_length=500)]
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: AnyHttpUrl

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("description must not be blank")
        return stripped


class PaymentAccepted(BaseModel):
    payment_id: UUID
    status: PaymentStatus
    created_at: datetime | None

    @classmethod
    def from_domain(cls, payment: Payment) -> "PaymentAccepted":
        if payment.id is None:
            raise ValueError("Payment must have an ID")
        return cls(
            payment_id=payment.id,
            status=payment.status,
            created_at=payment.created_at,
        )


class PaymentRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    webhook_url: str
    created_at: datetime | None
    processed_at: datetime | None
    webhook_delivered_at: datetime | None
    webhook_attempts: int
    last_webhook_error: str | None

    @classmethod
    def from_domain(cls, payment: Payment) -> "PaymentRead":
        if payment.id is None:
            raise ValueError("Payment must have an ID")
        return cls(
            id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            description=payment.description,
            metadata=payment.metadata,
            status=payment.status,
            webhook_url=payment.webhook_url,
            created_at=payment.created_at,
            processed_at=payment.processed_at,
            webhook_delivered_at=payment.webhook_delivered_at,
            webhook_attempts=payment.webhook_attempts,
            last_webhook_error=payment.last_webhook_error,
        )
