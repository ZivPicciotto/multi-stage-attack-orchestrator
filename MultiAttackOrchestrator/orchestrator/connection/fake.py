"""An in-memory stand-in for a real device. Same interface and failure vocabulary the Part 2
TCP client will have, so orchestration code can't tell the two apart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from orchestrator.connection.base import ConnectionLostError, RemoteFileError
from orchestrator.models.device import DeviceInfo, IOSVersion
from orchestrator.models.results import StageResult

logger = logging.getLogger(__name__)

DROP: Literal["DROP"] = "DROP"
"""Sentinel scripted in place of a StageResult: this call drops the connection instead."""

StageEvent = StageResult | Literal["DROP"]


@dataclass
class DeviceState:
    """Mutable device state, shared across every connection a provider hands out for one target.

    Held mutable (not a frozen snapshot) so that re-reading info before each attack attempt can
    genuinely observe drift — a drained battery, a device that's gone offline — rather than
    replaying a static fixture.
    """

    model: str
    ios_version: IOSVersion
    battery_level: int
    alive: bool = True
    filesystem: dict[str, bytes] = field(default_factory=dict)

    def drain_battery(self, amount: int) -> None:
        self.battery_level = max(0, self.battery_level - amount)


@dataclass
class ScriptedBehavior:
    """Deterministic, scripted device behavior — no luck involved, every test/demo path exact.

    `stage_events[stage_id]` is a queue popped in order on each `run_stage(stage_id)` call; once
    exhausted (or if never scripted), a stage defaults to repeatable success. `battery_drain`
    optionally costs battery on a stage attempt (successful or not), to script realistic state
    drift. `drop_on_read` names paths that drop the connection instead of returning bytes.
    """

    stage_events: dict[str, list[StageEvent]] = field(default_factory=dict)
    battery_drain: dict[str, int] = field(default_factory=dict)
    drop_on_read: frozenset[str] = frozenset()

    def next_stage_event(self, stage_id: str) -> StageEvent:
        queue = self.stage_events.get(stage_id)
        if queue:
            return queue.pop(0)
        return StageResult.ok()


class InMemoryDeviceConnection:
    """Structurally a DeviceConnection. Wraps shared DeviceState + ScriptedBehavior."""

    def __init__(
        self,
        state: DeviceState,
        behavior: ScriptedBehavior,
        timeout: float | None = None,
    ) -> None:
        self._state = state
        self._behavior = behavior
        self._timeout = timeout  # unused by the fake; carried for interface parity with Part 2

    def _require_alive(self) -> None:
        if not self._state.alive:
            raise ConnectionLostError("device is unreachable (connection previously dropped)")

    def get_device_info(self) -> DeviceInfo:
        self._require_alive()
        return DeviceInfo(
            model=self._state.model,
            ios_version=self._state.ios_version,
            battery_level=self._state.battery_level,
        )

    def run_stage(self, stage_id: str) -> StageResult:
        self._require_alive()
        event = self._behavior.next_stage_event(stage_id)

        drain = self._behavior.battery_drain.get(stage_id)
        if drain:
            self._state.drain_battery(drain)

        if event == DROP:
            self._state.alive = False
            logger.debug("device: stage %r dropped the connection", stage_id)
            raise ConnectionLostError(f"connection dropped during stage {stage_id!r}")

        assert isinstance(event, StageResult)
        if event.crashed:
            self._state.alive = False
            logger.debug("device: stage %r crashed the device", stage_id)
        else:
            logger.debug("device: stage %r -> %s", stage_id, "OK" if event.succeeded else "FAIL")
        return event

    def list_files(self) -> list[str]:
        self._require_alive()
        return sorted(self._state.filesystem)

    def read_file(self, path: str) -> bytes:
        self._require_alive()
        if path in self._behavior.drop_on_read:
            self._state.alive = False
            logger.debug("device: read %r dropped the connection", path)
            raise ConnectionLostError(f"connection dropped while reading {path!r}")
        try:
            return self._state.filesystem[path]
        except KeyError:
            raise RemoteFileError(f"no such file: {path!r}") from None

    def close(self) -> None:
        # State (and thus `alive`) is shared across connections for the same target and outlives
        # any single connection object — closing this handle doesn't kill the device.
        pass
