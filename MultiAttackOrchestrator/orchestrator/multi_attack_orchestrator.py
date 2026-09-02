"""Ties everything together: connect, learn the device, pick attacks, try them in order,
extract on the first win, report the whole run as one MultiAttackResult.
"""

from __future__ import annotations

import logging

from orchestrator.config import OrchestratorConfig
from orchestrator.connection.base import ConnectionLostError, ProtocolError
from orchestrator.connection.provider import DeviceConnectionProvider
from orchestrator.connection.session import DeviceSession
from orchestrator.device_info import DeviceInfoProvider
from orchestrator.execution import SingleAttackOrchestrator
from orchestrator.extraction import DataExtractor
from orchestrator.models.device import DeviceInfo
from orchestrator.models.phases import OrchestrationPhase
from orchestrator.models.results import AttackResult, MultiAttackResult
from orchestrator.resolver import AttackResolver

logger = logging.getLogger(__name__)


class MultiAttackOrchestrator:
    """Dependencies are injected so tests (and the demo) can supply a scripted
    MockConnectionProvider while the real logic runs unchanged. In Part 2, passing a
    TcpConnectionProvider is the only thing that would need to move."""

    def __init__(
        self,
        provider: DeviceConnectionProvider,
        resolver: AttackResolver | None = None,
        info_provider: DeviceInfoProvider | None = None,
        single: SingleAttackOrchestrator | None = None,
        extractor: DataExtractor | None = None,
    ) -> None:
        self.provider = provider
        self.resolver = resolver or AttackResolver()
        self.info_provider = info_provider or DeviceInfoProvider()
        self.single = single or SingleAttackOrchestrator()
        self.extractor = extractor or DataExtractor()

    def run(self, config: OrchestratorConfig) -> MultiAttackResult:
        attempts: list[AttackResult] = []
        phase = OrchestrationPhase.CONNECTING
        logger.info("orchestrator: connecting to %s:%d", config.target.host, config.target.port)

        with DeviceSession(self.provider, config.target) as session:
            try:
                phase = OrchestrationPhase.GATHERING_INFO
                info = self.info_provider.get_info(session.connection)
            except ConnectionLostError as e:
                logger.error("orchestrator: failed to gather device info: %s", e)
                return MultiAttackResult.failure(
                    config.request.mode, final_phase=phase, error=str(e)
                )

            phase = OrchestrationPhase.RESOLVING_ATTACKS
            candidates = self.resolver.resolve(info)
            if not candidates:
                logger.warning("orchestrator: no compatible attack for this device")
                return MultiAttackResult.failure(
                    config.request.mode,
                    final_phase=phase,
                    error="no compatible attack for this device",
                )

            try:
                for attack in candidates:
                    current_info = self._recheck(session)
                    if current_info is None:
                        logger.warning("attack %r: skipped — device unreachable", attack.id)
                        attempts.append(AttackResult.skipped(attack.id, "device unreachable"))
                        continue
                    if not attack.requirements.matches(current_info):
                        reasons = "; ".join(attack.requirements.reasons_incompatible(current_info))
                        logger.info("attack %r: skipped — %s", attack.id, reasons)
                        attempts.append(AttackResult.skipped(attack.id, reasons))
                        continue

                    phase = OrchestrationPhase.RUNNING_ATTACK
                    logger.info("attack %r: attempting", attack.id)
                    result = self.single.run(attack, session)
                    attempts.append(result)

                    if result.succeeded:
                        phase = OrchestrationPhase.EXTRACTING_DATA
                        extraction = self.extractor.extract(config.request, session.connection)
                        logger.info(
                            "orchestrator: done — winning attack %r, extraction succeeded=%s",
                            attack.id,
                            extraction.succeeded,
                        )
                        return MultiAttackResult.success(
                            config.request.mode,
                            winning_attack=attack.id,
                            attempts=tuple(attempts),
                            extraction=extraction,
                        )
            except ProtocolError as e:
                # The two sides desynced on the wire — a bug no retry fixes. Fatal to the run,
                # caught once here rather than left to crash the caller.
                logger.error("orchestrator: protocol desync at phase %s: %s", phase.value, e)
                return MultiAttackResult.failure(
                    config.request.mode,
                    final_phase=phase,
                    error=f"protocol desync: {e}",
                    attempts=tuple(attempts),
                )

            logger.warning("orchestrator: all viable attacks failed")
            return MultiAttackResult.failure(
                config.request.mode,
                final_phase=OrchestrationPhase.RUNNING_ATTACK,
                error="all viable attacks failed",
                attempts=tuple(attempts),
            )

    def _recheck(self, session: DeviceSession) -> DeviceInfo | None:
        try:
            return self.info_provider.get_info(session.connection)
        except ConnectionLostError:
            try:
                session.reconnect()
                return self.info_provider.get_info(session.connection)
            except ConnectionLostError:
                return None
