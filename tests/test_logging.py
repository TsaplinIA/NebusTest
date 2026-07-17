import json
import logging
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.commands import CreatePaymentCommand
from app.application.services import CreatePaymentService
from app.core.logging import JsonFormatter
from app.domain import Currency
from app.main import app
from tests.test_application_services import FakeUnitOfWork, Store


def test_json_formatter_includes_structured_extra_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_NAME", "test-service")
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="payment_created",
        args=(),
        exc_info=None,
    )
    record.event = "payment_created"
    record.payment_id = "payment-1"
    record.message_id = "message-1"
    record.attempt = 2

    payload = json.loads(formatter.format(record))

    assert payload["service"] == "test-service"
    assert payload["event"] == "payment_created"
    assert payload["payment_id"] == "payment-1"
    assert payload["message_id"] == "message-1"
    assert payload["attempt"] == 2


async def test_api_key_value_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "super-secret-api-key"
    caplog.set_level(logging.WARNING)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-API-Key": secret})

    assert response.status_code == 401
    assert "api_auth_failed" in caplog.text
    assert secret not in caplog.text


async def test_application_logs_key_events_without_full_idempotency_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = Store()
    service = CreatePaymentService(lambda: FakeUnitOfWork(store))
    secret_key = "client-secret-idempotency-key"
    caplog.set_level(logging.INFO)

    await service(
        CreatePaymentCommand(
            amount=Decimal("10.00"),
            currency=Currency.RUB,
            description="Order 42",
            metadata={"order_id": "42"},
            webhook_url="https://example.com/webhooks/payments",
            idempotency_key=secret_key,
        )
    )

    events = {record.__dict__.get("event") for record in caplog.records}
    assert "payment_create_requested" in events
    assert "payment_created" in events
    assert "outbox_event_created" in events
    assert secret_key not in caplog.text
    assert any(record.__dict__.get("idempotency_key_hash") for record in caplog.records)
