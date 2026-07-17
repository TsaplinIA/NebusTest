from dataclasses import dataclass
from enum import StrEnum


class GatewayDecision(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GatewayResult:
    decision: GatewayDecision
