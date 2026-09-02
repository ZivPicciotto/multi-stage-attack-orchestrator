"""The one contract the framework uses to talk to 'a device' """

from __future__ import annotations

from typing import Protocol

from orchestrator.models.device import DeviceInfo
from orchestrator.models.results import StageResult


class DeviceError(Exception):
    """Base class for every fault a DeviceConnection can raise."""


class ConnectionLostError(DeviceError):
    """The transport died: crash, unplug, drop. The session is dead; reconnect to continue."""


class ConnectionTimeout(ConnectionLostError):
    """An I/O call exceeded its deadline. Treated identically to a drop by the orchestrator."""


class RemoteFileError(DeviceError):
    """A single file is missing or inaccessible. Not fatal to the session — keep going."""


class ProtocolError(DeviceError):
    """The two sides disagree on the wire format. No retry fixes this; fatal to the whole run."""


class DeviceConnection(Protocol):
    """Everything the framework needs from a device. Every method may raise ConnectionLostError
    (including its ConnectionTimeout subtype) if the transport dies mid-call."""

    def get_device_info(self) -> DeviceInfo: ...

    def run_stage(self, stage_id: str) -> StageResult:
        """Attempt one stage. The device is authoritative: the returned StageResult says whether
        it succeeded, failed cleanly, or failed and crashed the device (StageResult.crashed)."""
        ...

    def list_files(self) -> list[str]: ...

    def read_file(self, path: str) -> bytes:
        """May also raise RemoteFileError if the path doesn't exist or isn't accessible."""
        ...

    def close(self) -> None: ...
