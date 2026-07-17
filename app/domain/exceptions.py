class DomainError(Exception):
    """Base class for domain-level errors."""


class InvalidPaymentAmountError(DomainError):
    """Raised when payment amount violates money rules."""


class InvalidPaymentStatusTransitionError(DomainError):
    """Raised when a payment status transition is not allowed."""
