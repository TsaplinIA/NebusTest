import asyncio

from app.application.services import PublishOutboxBatchService
from app.core.config import get_settings
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.messaging.broker import create_broker
from app.infrastructure.messaging.outbox_runner import OutboxRelayRunner
from app.infrastructure.messaging.publisher import RabbitOutboxPublisher
from app.infrastructure.messaging.topology import build_topology, declare_topology


async def main() -> None:
    settings = get_settings()
    broker = create_broker(settings)
    topology = build_topology(settings)
    await broker.connect()
    await declare_topology(broker, topology)

    publisher = RabbitOutboxPublisher(broker, topology)
    service = PublishOutboxBatchService(SqlAlchemyUnitOfWork, publisher)
    runner = OutboxRelayRunner(service, settings)

    try:
        await runner.run_forever()
    finally:
        await broker.stop()


if __name__ == "__main__":
    asyncio.run(main())
