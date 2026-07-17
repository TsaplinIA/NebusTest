from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.application.exceptions import IdempotencyConflictError, PaymentNotFoundError


async def payment_not_found_handler(
    request: Request,
    exc: PaymentNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": "Payment not found"},
    )


async def idempotency_conflict_handler(
    request: Request,
    exc: IdempotencyConflictError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "Idempotency key was reused with another payload"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PaymentNotFoundError, payment_not_found_handler)
    app.add_exception_handler(IdempotencyConflictError, idempotency_conflict_handler)
