"""An attack: an ordered chain of stages plus the device requirements to run it."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.models.device import DeviceCompatibilityReqs
from orchestrator.models.results import StageResult

if TYPE_CHECKING:
    # Imported for typing only; the concrete protocol lands in phase 2 (connection layer).
    from orchestrator.connection.base import DeviceConnection
    from orchestrator.models.context import SingleAttackSharedContext


@dataclass
class SingleStage:
    name: str
    stage_id: str  # command sent to the device to run this step
    success_probability: float  # the attacker's ESTIMATE — used only for ranking
    max_retries: int = 0  # ADDITIONAL in-place attempts after the first, on a clean failure

    def attempt(
        self,
        connection: DeviceConnection,
        context: SingleAttackSharedContext,
    ) -> StageResult:
        # The device decides reality — including whether a failure crashed it (result.crashed).
        # The stage just relays the verdict and stashes any payload. A dropped connection raises
        # ConnectionLostError, which propagates to the orchestrator.
        result = connection.run_stage(self.stage_id)
        if result.succeeded and result.payload is not None:
            context.set(self.name, result.payload)
        return result


@dataclass(frozen=True)
class Attack:
    id: str
    stages: tuple[SingleStage, ...]
    requirements: DeviceCompatibilityReqs
    max_restarts: int = 1  # full-chain restarts allowed (the cost-of-failure knob)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError(f"attack {self.id!r} must have at least one stage")

    @property
    def overall_probability(self) -> float:
        # Independent-events assumption: product of per-stage estimates. Ranking metric.
        return math.prod(s.success_probability for s in self.stages)
