from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.application.commands import CreatePaymentCommand
from app.application.services import CreatePaymentService, GetPaymentService
from app.presentation.dependencies import get_idempotency_key, verify_api_key
from app.presentation.services import (
    get_create_payment_service,
    get_get_payment_service,
)
from app.schemas.payments import PaymentAccepted, PaymentCreate, PaymentRead

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(verify_api_key)],
)

@router.post(
    "",
    response_model=PaymentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_payment(
    payload: PaymentCreate,
    idempotency_key: Annotated[str, Depends(get_idempotency_key)],
    service: Annotated[CreatePaymentService, Depends(get_create_payment_service)],
) -> PaymentAccepted:
    command = CreatePaymentCommand(
        amount=Decimal(payload.amount),
        currency=payload.currency,
        description=payload.description.strip(),
        metadata=payload.metadata,
        webhook_url=str(payload.webhook_url),
        idempotency_key=idempotency_key,
    )
    payment = await service(command)
    return PaymentAccepted.from_domain(payment)


@router.get("/{payment_id}", response_model=PaymentRead)
async def get_payment(
    payment_id: UUID,
    service: Annotated[GetPaymentService, Depends(get_get_payment_service)],
) -> PaymentRead:
    payment = await service(payment_id)
    return PaymentRead.from_domain(payment)
