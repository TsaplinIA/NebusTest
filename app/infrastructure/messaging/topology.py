from dataclasses import dataclass

from faststream.rabbit import RabbitExchange, RabbitQueue
from faststream.rabbit.schemas.constants import ExchangeType

from app.core.config import Settings
from app.infrastructure.messaging.retry import RetryPolicy

PAYMENTS_ROUTING_KEY = "payments.new"


@dataclass(frozen=True, slots=True)
class RabbitTopology:
    events_exchange: RabbitExchange
    retry_exchange: RabbitExchange
    dead_letter_exchange: RabbitExchange
    payments_queue: RabbitQueue
    retry_queues: tuple[RabbitQueue, ...]
    dead_letter_queue: RabbitQueue


def build_retry_queue(
    *,
    queue_name: str,
    events_exchange_name: str,
    delay_seconds: float,
) -> RabbitQueue:
    return RabbitQueue(
        queue_name,
        durable=True,
        routing_key=queue_name,
        arguments={
            "x-message-ttl": int(delay_seconds * 1000),
            "x-dead-letter-exchange": events_exchange_name,
            "x-dead-letter-routing-key": PAYMENTS_ROUTING_KEY,
        },
    )


def build_topology(settings: Settings) -> RabbitTopology:
    retry_policy = RetryPolicy(
        max_attempts=settings.message_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
    )

    events_exchange = RabbitExchange(
        settings.payments_exchange,
        type=ExchangeType.DIRECT,
        durable=True,
    )
    retry_exchange = RabbitExchange(
        settings.payments_retry_exchange,
        type=ExchangeType.DIRECT,
        durable=True,
    )
    dead_letter_exchange = RabbitExchange(
        settings.payments_dead_letter_exchange,
        type=ExchangeType.DIRECT,
        durable=True,
    )
    payments_queue = RabbitQueue(
        settings.payments_queue,
        durable=True,
        routing_key=PAYMENTS_ROUTING_KEY,
        arguments={
            "x-dead-letter-exchange": settings.payments_dead_letter_exchange,
            "x-dead-letter-routing-key": settings.payments_dead_letter_queue,
        },
    )
    retry_queues = tuple(
        build_retry_queue(
            queue_name=retry_policy.retry_queue_name(settings.payments_queue, attempt),
            events_exchange_name=settings.payments_exchange,
            delay_seconds=retry_policy.base_delay_seconds * (2 ** (attempt - 2)),
        )
        for attempt in range(2, settings.message_max_attempts + 1)
    )
    dead_letter_queue = RabbitQueue(
        settings.payments_dead_letter_queue,
        durable=True,
        routing_key=settings.payments_dead_letter_queue,
    )
    return RabbitTopology(
        events_exchange=events_exchange,
        retry_exchange=retry_exchange,
        dead_letter_exchange=dead_letter_exchange,
        payments_queue=payments_queue,
        retry_queues=retry_queues,
        dead_letter_queue=dead_letter_queue,
    )


async def declare_topology(broker, topology: RabbitTopology) -> None:
    events_exchange = await broker.declare_exchange(topology.events_exchange)
    retry_exchange = await broker.declare_exchange(topology.retry_exchange)
    dead_letter_exchange = await broker.declare_exchange(topology.dead_letter_exchange)
    payments_queue = await broker.declare_queue(topology.payments_queue)
    await payments_queue.bind(
        events_exchange,
        routing_key=topology.payments_queue.routing(),
        arguments=topology.payments_queue.bind_arguments,
    )
    for retry_queue in topology.retry_queues:
        declared_retry_queue = await broker.declare_queue(retry_queue)
        await declared_retry_queue.bind(
            retry_exchange,
            routing_key=retry_queue.routing(),
            arguments=retry_queue.bind_arguments,
        )
    dead_letter_queue = await broker.declare_queue(topology.dead_letter_queue)
    await dead_letter_queue.bind(
        dead_letter_exchange,
        routing_key=topology.dead_letter_queue.routing(),
        arguments=topology.dead_letter_queue.bind_arguments,
    )
