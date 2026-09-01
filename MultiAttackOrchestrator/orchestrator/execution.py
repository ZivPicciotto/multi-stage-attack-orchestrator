"""Runs one attack's stage chain: retries in place, restarts on crash/drop, reports the verdict.

Stages are dumb — metadata plus a single attempt. This is where all control flow lives: retry
or abort, restart or give up. Split into two functions on purpose: `_run_chain_once` runs the
stages of a single attempt top to bottom, and `run` wraps it in the restart loop. Keeping them
separate makes both independently reasoned about, with no `goto`-style branching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto

from orchestrator.connection.base import ConnectionLostError
from orchestrator.connection.session import DeviceSession
from orchestrator.models.attack import Attack
from orchestrator.models.context import SingleAttackSharedContext
from orchestrator.models.results import AttackResult

logger = logging.getLogger(__name__)


class _ChainOutcomeKind(Enum):
    SUCCESS = auto()
    GIVE_UP = auto()
    NEEDS_RESTART = auto()


@dataclass(frozen=True)
class _ChainOutcome:
    kind: _ChainOutcomeKind
    stage: str | None = None
    reason: str | None = None

    @classmethod
    def success(cls) -> "_ChainOutcome":
        return cls(_ChainOutcomeKind.SUCCESS)

    @classmethod
    def give_up(cls, stage: str, reason: str | None) -> "_ChainOutcome":
        return cls(_ChainOutcomeKind.GIVE_UP, stage, reason)

    @classmethod
    def needs_restart(cls, stage: str, reason: str) -> "_ChainOutcome":
        return cls(_ChainOutcomeKind.NEEDS_RESTART, stage, reason)


class SingleAttackOrchestrator:
    def run(self, attack: Attack, session: DeviceSession) -> AttackResult:
        restarts = 0
        while True:
            context = SingleAttackSharedContext()  # fresh per chain attempt: a restart = device reset
            outcome = self._run_chain_once(attack, session, context)

            if outcome.kind is _ChainOutcomeKind.SUCCESS:
                logger.info("attack %r: succeeded (restarts_used=%d)", attack.id, restarts)
                return AttackResult.success(attack.id, restarts_used=restarts)

            if outcome.kind is _ChainOutcomeKind.GIVE_UP:
                logger.info(
                    "attack %r: giving up at stage %r (%s)",
                    attack.id,
                    outcome.stage,
                    outcome.reason,
                )
                return AttackResult.failed(
                    attack.id, outcome.stage, outcome.reason or "unknown", restarts_used=restarts
                )

            # NEEDS_RESTART
            if restarts >= attack.max_restarts:
                reason = f"{outcome.reason}; restart budget exhausted"
                logger.info("attack %r: %s", attack.id, reason)
                return AttackResult.failed(
                    attack.id, outcome.stage, reason, restarts_used=restarts
                )
            restarts += 1
            logger.info(
                "attack %r: restarting whole chain (%s) — restart #%d/%d",
                attack.id,
                outcome.reason,
                restarts,
                attack.max_restarts,
            )
            session.reconnect()

    def _run_chain_once(
        self,
        attack: Attack,
        session: DeviceSession,
        context: SingleAttackSharedContext,
    ) -> _ChainOutcome:
        for stage in attack.stages:
            attempt = 0
            while True:
                logger.info(
                    "attack %r: stage %r attempt %d/%d",
                    attack.id,
                    stage.name,
                    attempt + 1,
                    stage.max_retries + 1,
                )
                try:
                    result = stage.attempt(session.connection, context)
                except ConnectionLostError as e:
                    return _ChainOutcome.needs_restart(stage.name, f"connection lost: {e}")

                if result.succeeded:
                    logger.info("attack %r: stage %r succeeded", attack.id, stage.name)
                    break  # advance to next stage

                # logical failure — the device's verdict says whether it also crashed:
                if result.crashed:
                    return _ChainOutcome.needs_restart(
                        stage.name, result.reason or "device crashed"
                    )
                if attempt < stage.max_retries:
                    attempt += 1
                    logger.info(
                        "attack %r: stage %r failed (%s) — retrying in place",
                        attack.id,
                        stage.name,
                        result.reason,
                    )
                    continue
                return _ChainOutcome.give_up(stage.name, result.reason)
        return _ChainOutcome.success()
