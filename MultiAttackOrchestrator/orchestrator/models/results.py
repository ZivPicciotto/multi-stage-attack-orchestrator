"""The result vocabulary produced at each layer of a run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orchestrator.models.extraction import ExtractionMode
from orchestrator.models.phases import OrchestrationPhase


@dataclass(frozen=True)
class StageResult:
    """One stage attempt's verdict. Returned by the connection, passed through by the stage.

    Whether a failure *crashed* the device is the device's verdict, not a fixed property of the
    stage: the same exploit can fail cleanly one attempt and panic the device the next. The
    orchestrator retries a clean failure in place but must restart the whole chain on a crash.
    """

    succeeded: bool
    payload: bytes | None = None  # data the device returned on success (-> shared context)
    reason: str | None = None  # human-readable explanation on failure
    crashed: bool = False  # a failure that also left the device unusable (panic / reboot)

    @classmethod
    def ok(cls, payload: bytes | None = None) -> StageResult:
        return cls(succeeded=True, payload=payload)

    @classmethod
    def fail(cls, reason: str) -> StageResult:
        """A clean logical failure — the device is intact and the stage may be retried in place."""
        return cls(succeeded=False, reason=reason)

    @classmethod
    def crash(cls, reason: str) -> StageResult:
        """A failure that also crashed the device — the chain must restart on a fresh connection."""
        return cls(succeeded=False, reason=reason, crashed=True)


class AttackStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class AttackResult:
    attack_id: str
    status: AttackStatus
    failed_stage: str | None = None  # set on FAILED
    reason: str | None = None
    restarts_used: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status is AttackStatus.SUCCESS

    @classmethod
    def success(cls, attack_id: str, restarts_used: int = 0) -> AttackResult:
        return cls(attack_id, AttackStatus.SUCCESS, restarts_used=restarts_used)

    @classmethod
    def failed(
        cls,
        attack_id: str,
        failed_stage: str | None,
        reason: str,
        restarts_used: int = 0,
    ) -> AttackResult:
        return cls(
            attack_id,
            AttackStatus.FAILED,
            failed_stage=failed_stage,
            reason=reason,
            restarts_used=restarts_used,
        )

    @classmethod
    def skipped(cls, attack_id: str, reason: str) -> AttackResult:
        return cls(attack_id, AttackStatus.SKIPPED, reason=reason)


@dataclass(frozen=True)
class FileResult:
    path: str
    succeeded: bool
    data: bytes | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExtractionOutcome:
    mode: ExtractionMode
    files: tuple[FileResult, ...] = ()
    error: str | None = None  # set if the session died mid-extraction

    @property
    def succeeded(self) -> bool:
        if self.error is not None:
            return False
        if self.mode is ExtractionMode.UNLOCK:
            return True
        return all(f.succeeded for f in self.files)

    @property
    def partial(self) -> bool:
        any_ok = any(f.succeeded for f in self.files)
        any_bad = self.error is not None or any(not f.succeeded for f in self.files)
        return any_ok and any_bad


@dataclass(frozen=True)
class MultiAttackResult:
    requested_mode: ExtractionMode
    final_phase: OrchestrationPhase
    succeeded: bool
    winning_attack: str | None
    attempts: tuple[AttackResult, ...]  # every attack tried, in order
    extraction: ExtractionOutcome | None = None
    error: str | None = None

    @classmethod
    def success(
        cls,
        requested_mode: ExtractionMode,
        winning_attack: str,
        attempts: tuple[AttackResult, ...],
        extraction: ExtractionOutcome,
        final_phase: OrchestrationPhase = OrchestrationPhase.DONE,
    ) -> MultiAttackResult:
        return cls(
            requested_mode,
            final_phase,
            True,
            winning_attack,
            tuple(attempts),
            extraction,
        )

    @classmethod
    def failure(
        cls,
        requested_mode: ExtractionMode,
        final_phase: OrchestrationPhase,
        error: str,
        attempts: tuple[AttackResult, ...] = (),
    ) -> MultiAttackResult:
        return cls(
            requested_mode,
            final_phase,
            False,
            None,
            tuple(attempts),
            None,
            error,
        )
