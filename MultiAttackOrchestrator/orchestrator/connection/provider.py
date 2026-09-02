"""Hands out connections to a target. The one thing that changes between Part 1 and Part 2."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from orchestrator.connection.base import DeviceConnection
from orchestrator.connection.mock import DeviceState, InMemoryDeviceConnection, ScriptedBehavior

if TYPE_CHECKING:
    from orchestrator.config import ConnectionTarget


class DeviceConnectionProvider(Protocol):
    def connect(self, target: "ConnectionTarget") -> DeviceConnection: ...


class MockConnectionProvider:
    """Hands out fresh InMemoryDeviceConnections wrapping one shared DeviceState + Behavior.

    Sharing state/behavior across connect() calls is what makes a reconnect resume correctly:
    a stage's scripted queue continues where it left off, and device attributes (e.g. battery)
    persist across a crash-restart exactly as they would on a real device.
    """

    def __init__(
        self,
        state: DeviceState,
        behavior: ScriptedBehavior,
        timeout: float | None = None,
    ) -> None:
        self._state = state
        self._behavior = behavior
        self._timeout = timeout
        self.connect_count = 0

    def connect(self, target: "ConnectionTarget") -> DeviceConnection:
        # target is unused by the mock (no real networking) — kept for interface parity with
        # the Part 2 TcpConnectionProvider, which needs it to actually open a socket.
        self._state.alive = True  # a fresh connect() represents comms being (re-)established
        self.connect_count += 1
        return InMemoryDeviceConnection(self._state, self._behavior, self._timeout)
