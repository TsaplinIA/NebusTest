from dataclasses import dataclass

ATTEMPT_HEADER = "x-attempt"
ERROR_HEADER = "x-error"
ORIGINAL_MESSAGE_ID_HEADER = "x-original-message-id"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    next_attempt: int | None = None
    delay_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be greater than zero")

    def decide(self, current_attempt: int) -> RetryDecision:
        if current_attempt < 1:
            current_attempt = 1
        if current_attempt >= self.max_attempts:
            return RetryDecision(retry=False)

        next_attempt = current_attempt + 1
        delay_seconds = self.base_delay_seconds * (2 ** (next_attempt - 2))
        return RetryDecision(
            retry=True,
            next_attempt=next_attempt,
            delay_seconds=delay_seconds,
        )

    def retry_queue_name(self, base_queue: str, attempt: int) -> str:
        return f"{base_queue}.retry.{attempt}"


def parse_attempt(headers: dict[str, object] | None) -> int:
    if not headers:
        return 1
    value = headers.get(ATTEMPT_HEADER)
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 1
