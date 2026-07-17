from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.webhook_echo import app


@pytest.fixture
async def echo_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://echo") as client:
        yield client


async def test_echo_service_logs_and_returns_request_parts(
    echo_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")

    response = await echo_client.post(
        "/webhooks/payments?foo=one&foo=two",
        headers={"X-Webhook-Id": "delivery-1"},
        json={"payment_id": "payment-1", "status": "succeeded"},
    )

    body = response.json()
    request = body["request"]
    assert response.status_code == 200
    assert body["received"] is True
    assert request["method"] == "POST"
    assert request["path"] == "/webhooks/payments"
    assert request["path_param"] == "webhooks/payments"
    assert request["query_params"] == {"foo": ["one", "two"]}
    assert request["headers"]["x-webhook-id"] == "delivery-1"
    assert request["body"] == {"payment_id": "payment-1", "status": "succeeded"}
    assert request["body_size"] > 0
    assert any(
        record.__dict__.get("event") == "webhook_echo_request_received"
        and record.__dict__.get("path") == "/webhooks/payments"
        for record in caplog.records
    )


async def test_echo_service_health(echo_client: AsyncClient) -> None:
    response = await echo_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "webhook-echo"}
