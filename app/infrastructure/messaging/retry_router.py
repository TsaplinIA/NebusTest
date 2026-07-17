import logging
from dataclasses import dataclass

from app.core.config import Settings
from app.infrastructure.messaging.retry import (
    ATTEMPT_HEADER,
    ERROR_HEADER,
    ORIGINAL_MESSAGE_ID_HEADER,
    RetryPolicy,
)
from app.infrastructure.messaging.topology import PAYMENTS_ROUTING_KEY, RabbitTopology

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RabbitRetryRouter:
    broker: object
    topology: RabbitTopology
    settings: Settings

    @property
    def policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.settings.message_max_attempts,
            base_delay_seconds=self.settings.retry_base_delay_seconds,
        )

    async def retry_or_dlq(
        self,
        *,
        payload: dict[str, object],
        current_attempt: int,
        message_id: str | None,
        reason: str,
    ) -> str:
        decision = self.policy.decide(current_attempt)
        if not decision.retry or decision.next_attempt is None:
            await self.send_to_dlq(
                payload=payload,
                current_attempt=current_attempt,
                message_id=message_id,
                reason=reason,
            )
            return "dlq"

        retry_queue = self.policy.retry_queue_name(
            self.settings.payments_queue,
            decision.next_attempt,
        )
        logger.warning(
            "message_retry_scheduled",
            extra={
                "event": "message_retry_scheduled",
                "message_id": message_id,
                "attempt": decision.next_attempt,
                "delay_seconds": decision.delay_seconds,
                "reason": reason,
            },
        )
        await self.broker.publish(
            payload,
            exchange=self.topology.retry_exchange,
            routing_key=retry_queue,
            message_id=message_id,
            headers={
                ATTEMPT_HEADER: decision.next_attempt,
                ERROR_HEADER: reason,
                ORIGINAL_MESSAGE_ID_HEADER: message_id or "",
            },
            persist=True,
        )
        return "retry"

    async def send_to_dlq(
        self,
        *,
        payload: dict[str, object],
        current_attempt: int,
        message_id: str | None,
        reason: str,
    ) -> None:
        capped_attempt = min(max(current_attempt, 1), self.settings.message_max_attempts)
        logger.warning(
            "message_sent_to_dlq",
            extra={
                "event": "message_sent_to_dlq",
                "message_id": message_id,
                "attempt": capped_attempt,
                "reason": reason,
            },
        )
        await self.broker.publish(
            payload,
            exchange=self.topology.dead_letter_exchange,
            routing_key=self.settings.payments_dead_letter_queue,
            message_id=message_id,
            headers={
                ATTEMPT_HEADER: capped_attempt,
                ERROR_HEADER: reason,
                ORIGINAL_MESSAGE_ID_HEADER: message_id or "",
                "x-original-routing-key": PAYMENTS_ROUTING_KEY,
            },
            persist=True,
        )
