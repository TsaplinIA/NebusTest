import asyncio
import logging
from dataclasses import dataclass, field

from app.application.services import PublishOutboxBatchService
from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OutboxRelayRunner:
    service: PublishOutboxBatchService
    settings: Settings
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def run_once(self) -> int:
        return await self.service(self.settings.outbox_batch_size)

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                published = await self.run_once()
                if published:
                    logger.info("outbox_batch_published", extra={"published": published})
            except Exception:
                logger.exception("outbox_relay_iteration_failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.outbox_poll_interval_seconds,
                )
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()
