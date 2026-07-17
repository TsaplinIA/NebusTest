from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from app.application.exceptions import (
    PaymentNotFoundError,
    PermanentInfrastructureError,
    TemporaryInfrastructureError,
)
from app.infrastructure.messaging import consumer
from app.infrastructure.messaging.retry import ATTEMPT_HEADER


@dataclass(slots=True)
class RawMessage:
    headers: dict[str, object]
    message_id: str


@dataclass(slots=True)
class FakeMessage:
    raw_message: RawMessage
    acked: bool = False

    async def ack(self) -> None:
        self.acked = True


@dataclass(slots=True)
class RouteRecorder:
    retry_calls: list[dict] = field(default_factory=list)
    dlq_calls: list[dict] = field(default_factory=list)


def patch_retry_router(monkeypatch: pytest.MonkeyPatch, recorder: RouteRecorder) -> None:
    class FakeRetryRouter:
        def __init__(self, **kwargs) -> None:
            pass

        async def retry_or_dlq(self, **kwargs) -> str:
            recorder.retry_calls.append(kwargs)
            return "retry"

        async def send_to_dlq(self, **kwargs) -> None:
            recorder.dlq_calls.append(kwargs)

    monkeypatch.setattr(consumer, "RabbitRetryRouter", FakeRetryRouter)


def patch_process_service(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> None:
    class FakeProcessService:
        async def __call__(self, payment_id):
            if error is not None:
                raise error

    monkeypatch.setattr(consumer, "build_process_service", lambda: FakeProcessService())


async def test_consumer_routes_transient_error_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = RouteRecorder()
    patch_retry_router(monkeypatch, recorder)
    patch_process_service(monkeypatch, TemporaryInfrastructureError("timeout"))
    message = FakeMessage(RawMessage(headers={ATTEMPT_HEADER: 1}, message_id="msg-1"))
    payment_id = uuid4()

    await consumer.handle_payment_message({"payment_id": str(payment_id)}, message)

    assert message.acked is True
    assert len(recorder.retry_calls) == 1
    assert recorder.retry_calls[0]["current_attempt"] == 1
    assert recorder.retry_calls[0]["message_id"] == "msg-1"
    assert recorder.dlq_calls == []


async def test_consumer_routes_permanent_error_to_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = RouteRecorder()
    patch_retry_router(monkeypatch, recorder)
    patch_process_service(monkeypatch, PermanentInfrastructureError("bad request"))
    message = FakeMessage(RawMessage(headers={ATTEMPT_HEADER: 2}, message_id="msg-2"))
    payment_id = uuid4()

    await consumer.handle_payment_message({"payment_id": str(payment_id)}, message)

    assert message.acked is True
    assert recorder.retry_calls == []
    assert len(recorder.dlq_calls) == 1
    assert recorder.dlq_calls[0]["current_attempt"] == 2


async def test_consumer_routes_missing_payment_to_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = RouteRecorder()
    patch_retry_router(monkeypatch, recorder)
    patch_process_service(monkeypatch, PaymentNotFoundError("payment not found"))
    message = FakeMessage(RawMessage(headers={ATTEMPT_HEADER: 1}, message_id="msg-404"))
    payment_id = uuid4()

    await consumer.handle_payment_message({"payment_id": str(payment_id)}, message)

    assert message.acked is True
    assert recorder.retry_calls == []
    assert len(recorder.dlq_calls) == 1
    assert recorder.dlq_calls[0]["payload"] == {"payment_id": str(payment_id)}
    assert recorder.dlq_calls[0]["reason"] == "payment not found"


async def test_consumer_routes_malformed_payload_to_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = RouteRecorder()
    patch_retry_router(monkeypatch, recorder)
    patch_process_service(monkeypatch, None)
    message = FakeMessage(RawMessage(headers={ATTEMPT_HEADER: 1}, message_id="msg-3"))

    await consumer.handle_payment_message({"wrong": "payload"}, message)

    assert message.acked is True
    assert recorder.retry_calls == []
    assert len(recorder.dlq_calls) == 1
    assert recorder.dlq_calls[0]["reason"] == "malformed payment message"


async def test_consumer_normalizes_non_mapping_malformed_payload_to_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = RouteRecorder()
    patch_retry_router(monkeypatch, recorder)
    patch_process_service(monkeypatch, None)
    message = FakeMessage(RawMessage(headers={ATTEMPT_HEADER: "bad"}, message_id="msg-4"))

    await consumer.handle_payment_message("not-a-dict", message)  # type: ignore[arg-type]

    assert message.acked is True
    assert recorder.retry_calls == []
    assert len(recorder.dlq_calls) == 1
    assert recorder.dlq_calls[0]["current_attempt"] == 1
    assert recorder.dlq_calls[0]["payload"] == {"malformed_payload": "'not-a-dict'"}
