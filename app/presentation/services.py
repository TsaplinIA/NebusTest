from app.application.services import CreatePaymentService, GetPaymentService
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork


def get_create_payment_service() -> CreatePaymentService:
    return CreatePaymentService(SqlAlchemyUnitOfWork)


def get_get_payment_service() -> GetPaymentService:
    return GetPaymentService(SqlAlchemyUnitOfWork)
