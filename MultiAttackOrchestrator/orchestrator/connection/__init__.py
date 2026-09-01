"""The seam between the framework and 'the device' — a fake in Part 1, TCP in Part 2."""

from orchestrator.connection.base import (
    ConnectionLostError,
    ConnectionTimeout,
    DeviceConnection,
    DeviceError,
    ProtocolError,
    RemoteFileError,
)
from orchestrator.connection.fake import (
    DROP,
    DeviceState,
    InMemoryDeviceConnection,
    ScriptedBehavior,
)
from orchestrator.connection.provider import DeviceConnectionProvider, FakeConnectionProvider
from orchestrator.connection.session import DeviceSession

__all__ = [
    "DeviceConnection",
    "DeviceError",
    "ConnectionLostError",
    "ConnectionTimeout",
    "RemoteFileError",
    "ProtocolError",
    "DeviceState",
    "ScriptedBehavior",
    "DROP",
    "InMemoryDeviceConnection",
    "DeviceConnectionProvider",
    "FakeConnectionProvider",
    "DeviceSession",
]
