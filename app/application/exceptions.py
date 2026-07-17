class ApplicationError(Exception):
    """Base class for application-level errors."""


class PaymentNotFoundError(ApplicationError):
    """Raised when a requested payment does not exist."""


class IdempotencyConflictError(ApplicationError):
    """Raised when the same idempotency key is reused with another payload."""


class PersistenceConflictError(ApplicationError):
    """Raised when persistence constraints reject a write."""


class TemporaryInfrastructureError(ApplicationError):
    """Raised for retryable infrastructure failures."""


class PermanentInfrastructureError(ApplicationError):
    """Raised for non-retryable infrastructure failures."""
