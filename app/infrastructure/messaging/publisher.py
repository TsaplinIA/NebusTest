from app.application.outbox import OutboxEvent
from app.application.ports import EventPublisher
from app.infrastructure.messaging.retry import ATTEMPT_HEADER
from app.infrastructure.messaging.topology import PAYMENTS_ROUTING_KEY, RabbitTopology


class RabbitOutboxPublisher(EventPublisher):
    def __init__(self, broker, topology: RabbitTopology) -> None:
        self._broker = broker
        self._topology = topology

    async def publish(self, event: OutboxEvent) -> None:
        confirmation = await self._broker.publish(
            event.payload,
            exchange=self._topology.events_exchange,
            routing_key=PAYMENTS_ROUTING_KEY,
            message_id=str(event.id),
            headers={ATTEMPT_HEADER: 1},
            persist=True,
        )
        if confirmation is False:
            raise RuntimeError("RabbitMQ publish was not confirmed")
