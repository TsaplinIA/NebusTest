from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.enums import Currency, PaymentStatus
from app.domain.exceptions import (
    InvalidPaymentAmountError,
    InvalidPaymentStatusTransitionError,
)


@dataclass(slots=True)
class Payment:
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    webhook_url: str
    idempotency_key: str
    request_fingerprint: str
    id: UUID | None = None
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: datetime | None = None
    processed_at: datetime | None = None
    webhook_delivered_at: datetime | None = None
    webhook_attempts: int = 0
    last_webhook_error: str | None = None
    _final_statuses: set[PaymentStatus] = field(
        default_factory=lambda: {PaymentStatus.SUCCEEDED, PaymentStatus.FAILED},
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise InvalidPaymentAmountError("Payment amount must be Decimal")
        if self.amount <= Decimal("0"):
            raise InvalidPaymentAmountError("Payment amount must be greater than zero")
        self.currency = Currency(self.currency)
        self.status = PaymentStatus(self.status)

    @classmethod
    def create(
        cls,
        *,
        amount: Decimal,
        currency: Currency,
        description: str,
        metadata: dict[str, Any],
        webhook_url: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> "Payment":
        return cls(
            amount=amount,
            currency=currency,
            description=description,
            metadata=metadata,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status=PaymentStatus.PENDING,
        )

    @property
    def is_final(self) -> bool:
        return self.status in self._final_statuses

    def succeed(self, processed_at: datetime) -> None:
        self._ensure_can_transition(PaymentStatus.SUCCEEDED)
        self.status = PaymentStatus.SUCCEEDED
        self.processed_at = processed_at

    def fail(self, processed_at: datetime) -> None:
        self._ensure_can_transition(PaymentStatus.FAILED)
        self.status = PaymentStatus.FAILED
        self.processed_at = processed_at

    def _ensure_can_transition(self, target_status: PaymentStatus) -> None:
        if self.is_final:
            raise InvalidPaymentStatusTransitionError(
                f"Cannot change final payment status from {self.status} to {target_status}"
            )
        if self.status != PaymentStatus.PENDING:
            raise InvalidPaymentStatusTransitionError(
                f"Cannot change payment status from {self.status} to {target_status}"
            )
