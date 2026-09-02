"""Pure domain types for the orchestrator framework (no I/O, no control flow)."""

from orchestrator.models.attack import Attack, SingleStage
from orchestrator.models.context import SingleAttackSharedContext
from orchestrator.models.device import (
    DeviceCompatibilityReqs,
    DeviceInfo,
    IOSVersion,
)
from orchestrator.models.extraction import ExtractionMode, ExtractionRequest
from orchestrator.models.phases import OrchestrationPhase
from orchestrator.models.results import (
    AttackResult,
    AttackStatus,
    ExtractionOutcome,
    FileResult,
    MultiAttackResult,
    ResultType,
    StageResult,
)

__all__ = [
    "OrchestrationPhase",
    "IOSVersion",
    "DeviceInfo",
    "DeviceCompatibilityReqs",
    "SingleAttackSharedContext",
    "ExtractionMode",
    "ExtractionRequest",
    "StageResult",
    "ResultType",
    "AttackStatus",
    "AttackResult",
    "FileResult",
    "ExtractionOutcome",
    "MultiAttackResult",
    "SingleStage",
    "Attack",
]
