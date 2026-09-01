"""The single point that reads device attributes from a connection."""

from __future__ import annotations

from orchestrator.connection.base import DeviceConnection
from orchestrator.models.device import DeviceInfo


class DeviceInfoProvider:
    """Centralizing this call is what makes re-checking device state before every attack
    attempt (see MultiAttackOrchestrator) a single, testable responsibility rather than
    scattered `connection.get_device_info()` calls."""

    def get_info(self, connection: DeviceConnection) -> DeviceInfo:
        return connection.get_device_info()
