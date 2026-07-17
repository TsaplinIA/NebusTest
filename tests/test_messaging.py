from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.application.exceptions import (
    PermanentInfrastructureError,
    TemporaryInfrastructureError,
)
from app.application.gateway import GatewayDecision
from app.application.outbox import OutboxEvent
from app.core.config import Settings
from app.domain import Currency, Payment
from app.infrastructure.http.gateway import MockPaymentGateway
from app.infrastructure.http.webhook import HttpxWebhookClient
from app.infrastructure.messaging.publisher import RabbitOutboxPublisher
from app.infrastructure.messaging.retry import ATTEMPT_HEADER, RetryPolicy, parse_attempt
from app.infrastructure.messaging.retry_router import RabbitRetryRouter
from app.infrastructure.messaging.topology import build_topology


class FakeBroker:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, message=None, **kwargs):
        self.published.append({"message": message, **kwargs})
        return True


def settings() -> Settings:
    return Settings(
        MESSAGE_MAX_ATTEMPTS=3,
        RETRY_BASE_DELAY_SECONDS=2,
        API_KEY="change-me",
    )


def make_outbox_event() -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        event_type="payments.new",
        aggregate_type="payment",
        aggregate_id=uuid4(),
        payload={"payment_id": str(uuid4())},
        status="pending",
        attempts=0,
        created_at=datetime.now(UTC),
        published_at=None,
        last_error=None,
    )


def test_retry_policy_uses_exponential_delays_and_attempt_limit() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=2)

    first = policy.decide(1)
    second = policy.decide(2)
    third = policy.decide(3)

    assert first.retry is True
    assert first.next_attempt == 2
    assert first.delay_seconds == 2
    assert second.retry is True
    assert second.next_attempt == 3
    assert second.delay_seconds == 4
    assert third.retry is False


def test_parse_attempt_defaults_to_first_attempt() -> None:
    assert parse_attempt(None) == 1
    assert parse_attempt({}) == 1
    assert parse_attempt({ATTEMPT_HEADER: "bad"}) == 1
    assert parse_attempt({ATTEMPT_HEADER: 2}) == 2


async def test_rabbit_outbox_publisher_uses_stable_message_id() -> None:
    broker = FakeBroker()
    topology = build_topology(settings())
    event = make_outbox_event()
    publisher = RabbitOutboxPublisher(broker, topology)

    await publisher.publish(event)

    published = broker.published[0]
    assert published["message"] == event.payload
    assert published["message_id"] == str(event.id)
    assert published["routing_key"] == "payments.new"
    assert published["headers"][ATTEMPT_HEADER] == 1
    assert published["persist"] is True


async def test_retry_router_sends_attempt_two_to_retry_queue() -> None:
    current_settings = settings()
    broker = FakeBroker()
    topology = build_topology(current_settings)
    router = RabbitRetryRouter(broker=broker, topology=topology, settings=current_settings)

    route = await router.retry_or_dlq(
        payload={"payment_id": str(uuid4())},
        current_attempt=1,
        message_id="message-1",
        reason="timeout",
    )

    published = broker.published[0]
    assert route == "retry"
    assert published["routing_key"] == "payments.new.retry.2"
    assert published["headers"][ATTEMPT_HEADER] == 2
    assert published["headers"]["x-original-message-id"] == "message-1"


async def test_retry_router_sends_attempt_three_to_second_retry_queue() -> None:
    current_settings = settings()
    broker = FakeBroker()
    topology = build_topology(current_settings)
    router = RabbitRetryRouter(broker=broker, topology=topology, settings=current_settings)

    route = await router.retry_or_dlq(
        payload={"payment_id": str(uuid4())},
        current_attempt=2,
        message_id="message-1",
        reason="timeout again",
    )

    published = broker.published[0]
    assert route == "retry"
    assert published["routing_key"] == "payments.new.retry.3"
    assert published["headers"][ATTEMPT_HEADER] == 3
    assert published["headers"]["x-error"] == "timeout again"


async def test_retry_router_sends_third_failure_to_dlq() -> None:
    current_settings = settings()
    broker = FakeBroker()
    topology = build_topology(current_settings)
    router = RabbitRetryRouter(broker=broker, topology=topology, settings=current_settings)

    route = await router.retry_or_dlq(
        payload={"payment_id": str(uuid4())},
        current_attempt=3,
        message_id="message-1",
        reason="timeout",
    )

    published = broker.published[0]
    assert route == "dlq"
    assert published["routing_key"] == current_settings.payments_dead_letter_queue
    assert published["headers"][ATTEMPT_HEADER] == 3


async def test_retry_router_caps_excessive_attempt_in_dlq_headers() -> None:
    current_settings = settings()
    broker = FakeBroker()
    topology = build_topology(current_settings)
    router = RabbitRetryRouter(broker=broker, topology=topology, settings=current_settings)

    route = await router.retry_or_dlq(
        payload={"payment_id": str(uuid4())},
        current_attempt=99,
        message_id="message-1",
        reason="timeout",
    )

    assert route == "dlq"
    assert broker.published[0]["headers"][ATTEMPT_HEADER] == current_settings.message_max_attempts


async def test_mock_gateway_uses_injected_random_and_sleep() -> None:
    sleeps: list[float] = []
    values = iter([0.5, 0.89])

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    gateway = MockPaymentGateway(
        min_seconds=2,
        max_seconds=5,
        success_rate=0.9,
        random_fn=lambda: next(values),
        sleep=fake_sleep,
    )
    payment = Payment.create(
        amount=__import__("decimal").Decimal("10.00"),
        currency=Currency.RUB,
        description="Order 42",
        metadata={},
        webhook_url="https://example.com/webhooks/payments",
        idempotency_key="idem",
        request_fingerprint="fingerprint",
    )

    result = await gateway.process(payment)

    assert sleeps == [3.5]
    assert result.decision == GatewayDecision.SUCCEEDED


async def test_http_webhook_client_accepts_2xx() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Webhook-Id"] == "delivery-1"
        assert request.headers["Content-Type"] == "application/json"
        assert request.content == b'{"payment_id":"payment-1","status":"succeeded"}'
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        webhook = HttpxWebhookClient(client)
        await webhook.deliver(
            url="https://example.com/webhooks/payments",
            payload={"payment_id": "payment-1", "status": "succeeded"},
            headers={"X-Webhook-Id": "delivery-1"},
        )


@pytest.mark.parametrize("status_code", [408, 429, 500])
async def test_http_webhook_client_maps_retryable_statuses(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        webhook = HttpxWebhookClient(client)
        with pytest.raises(TemporaryInfrastructureError):
            await webhook.deliver(url="https://example.com", payload={}, headers={})


async def test_http_webhook_client_maps_permanent_statuses() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        webhook = HttpxWebhookClient(client)
        with pytest.raises(PermanentInfrastructureError):
            await webhook.deliver(url="https://example.com", payload={}, headers={})


def test_topology_contains_durable_retry_and_dlq_queues() -> None:
    current_settings = settings()
    topology = build_topology(current_settings)

    assert topology.payments_queue.durable is True
    assert len(topology.retry_queues) == 2
    assert topology.retry_queues[0].arguments["x-message-ttl"] == 2000
    assert topology.retry_queues[1].arguments["x-message-ttl"] == 4000
    assert topology.dead_letter_queue.name == current_settings.payments_dead_letter_queue
