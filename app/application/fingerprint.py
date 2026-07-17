import hashlib
import json
from decimal import Decimal
from typing import Any

from app.application.commands import CreatePaymentCommand


def normalize_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def normalize_for_fingerprint(command: CreatePaymentCommand) -> dict[str, Any]:
    return {
        "amount": normalize_decimal(command.amount),
        "currency": command.currency.value,
        "description": command.description,
        "metadata": command.metadata,
        "webhook_url": command.webhook_url,
    }


def create_request_fingerprint(command: CreatePaymentCommand) -> str:
    payload = json.dumps(
        normalize_for_fingerprint(command),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_idempotency_key_hash(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
