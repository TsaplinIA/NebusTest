import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass

from app.application.gateway import GatewayDecision, GatewayResult
from app.application.ports import PaymentGateway
from app.domain.payment import Payment

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MockPaymentGateway(PaymentGateway):
    min_seconds: float
    max_seconds: float
    success_rate: float
    random_fn: Callable[[], float] = random.random
    sleep: Callable[[float], object] = asyncio.sleep

    async def process(self, payment: Payment) -> GatewayResult:
        delay = self.min_seconds + (self.max_seconds - self.min_seconds) * self.random_fn()
        logger.info(
            "gateway_processing_started",
            extra={
                "event": "gateway_processing_started",
                "payment_id": str(payment.id),
                "delay_seconds": delay,
            },
        )
        await self.sleep(delay)
        decision = (
            GatewayDecision.SUCCEEDED
            if self.random_fn() < self.success_rate
            else GatewayDecision.FAILED
        )
        logger.info(
            "gateway_processing_finished",
            extra={
                "event": "gateway_processing_finished",
                "payment_id": str(payment.id),
                "decision": decision.value,
            },
        )
        return GatewayResult(decision=decision)
