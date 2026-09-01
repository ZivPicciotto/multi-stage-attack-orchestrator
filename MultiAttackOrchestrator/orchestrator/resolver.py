"""Decides which attacks are viable for a device, ranked best-first."""

from __future__ import annotations

import logging

from orchestrator.attacks.catalog import CATALOG
from orchestrator.models.attack import Attack
from orchestrator.models.device import DeviceInfo

logger = logging.getLogger(__name__)


class AttackResolver:
    """A pure function of (device info, catalog): filter by compatibility, rank by estimated
    overall success probability (product of stage probabilities), descending. Ties break toward
    fewer stages (less to go wrong), then attack id, for deterministic ordering."""

    def __init__(self, catalog: tuple[Attack, ...] = CATALOG) -> None:
        self._catalog = catalog

    def resolve(self, info: DeviceInfo) -> list[Attack]:
        viable = [a for a in self._catalog if a.requirements.matches(info)]
        ranked = sorted(viable, key=lambda a: (-a.overall_probability, len(a.stages), a.id))
        logger.info(
            "resolver: %d/%d attacks viable for %s (iOS %s, battery %d%%): %s",
            len(ranked),
            len(self._catalog),
            info.model,
            info.ios_version,
            info.battery_level,
            [f"{a.id} (p={a.overall_probability:.2f})" for a in ranked],
        )
        return ranked
