from faststream.rabbit import RabbitBroker

from app.core.config import Settings, get_settings


def create_broker(settings: Settings | None = None) -> RabbitBroker:
    settings = settings or get_settings()
    return RabbitBroker(settings.rabbitmq_url)
