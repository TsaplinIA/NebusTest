from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domain.enums import Currency


@dataclass(frozen=True, slots=True)
class CreatePaymentCommand:
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    webhook_url: str
    idempotency_key: str
