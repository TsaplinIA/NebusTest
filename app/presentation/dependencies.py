import logging
from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.application.idempotency import IDEMPOTENCY_KEY_MAX_LENGTH
from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    settings = get_settings()
    if x_api_key is None or not compare_digest(x_api_key, settings.api_key):
        logger.warning("api_auth_failed", extra={"event": "api_auth_failed"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


async def get_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Required idempotency key for payment creation.",
        ),
    ] = None,
) -> str:
    if idempotency_key is None:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key header is required",
        )
    normalized = idempotency_key.strip()
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key header must not be empty",
        )
    if len(normalized) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Idempotency-Key header must be at most {IDEMPOTENCY_KEY_MAX_LENGTH} characters",
        )
    return normalized
