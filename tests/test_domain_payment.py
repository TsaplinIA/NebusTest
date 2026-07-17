from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain import Currency, Payment, PaymentStatus
from app.domain.exceptions import (
    InvalidPaymentAmountError,
    InvalidPaymentStatusTransitionError,
)


def make_payment(amount: Decimal = Decimal("10.50")) -> Payment:
    return Payment.create(
        amount=amount,
        currency=Currency.RUB,
        description="Order 42",
        metadata={"order_id": "42"},
        webhook_url="https://example.com/webhooks/payments",
        idempotency_key="idem-key",
        request_fingerprint="fingerprint",
    )


def test_new_payment_is_pending_and_uses_decimal_amount() -> None:
    payment = make_payment()

    assert payment.status == PaymentStatus.PENDING
    assert payment.amount == Decimal("10.50")
    assert not payment.is_final


def test_payment_rejects_non_decimal_amount() -> None:
    with pytest.raises(InvalidPaymentAmountError):
        Payment.create(
            amount=10.50,  # type: ignore[arg-type]
            currency=Currency.RUB,
            description="Order 42",
            metadata={},
            webhook_url="https://example.com/webhooks/payments",
            idempotency_key="idem-key",
            request_fingerprint="fingerprint",
        )


def test_payment_rejects_non_positive_amount() -> None:
    with pytest.raises(InvalidPaymentAmountError):
        make_payment(Decimal("0"))


def test_payment_rejects_unknown_currency() -> None:
    with pytest.raises(ValueError):
        Payment.create(
            amount=Decimal("10.50"),
            currency="GBP",  # type: ignore[arg-type]
            description="Order 42",
            metadata={},
            webhook_url="https://example.com/webhooks/payments",
            idempotency_key="idem-key",
            request_fingerprint="fingerprint",
        )


def test_payment_can_succeed_from_pending() -> None:
    processed_at = datetime.now(UTC)
    payment = make_payment()

    payment.succeed(processed_at)

    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.processed_at == processed_at
    assert payment.is_final


def test_final_status_cannot_be_changed() -> None:
    payment = make_payment()
    payment.fail(datetime.now(UTC))

    with pytest.raises(InvalidPaymentStatusTransitionError):
        payment.succeed(datetime.now(UTC))
