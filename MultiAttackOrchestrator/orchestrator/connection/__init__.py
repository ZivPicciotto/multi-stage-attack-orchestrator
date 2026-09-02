"""The seam between the framework and 'the device' — a mock in Part 1, TCP in Part 2."""

from orchestrator.connection.base import (
    ConnectionLostError,
    ConnectionTimeout,
    DeviceConnection,
    DeviceError,
    ProtocolError,
    RemoteFileError,
)
from orchestrator.connection.mock import (
    DROP,
    DeviceState,
    InMemoryDeviceConnection,
    ScriptedBehavior,
)
from orchestrator.connection.provider import DeviceConnectionProvider, MockConnectionProvider
from orchestrator.connection.session import DeviceSession
from orchestrator.connection.tcp import TcpConnectionProvider, TcpDeviceConnection

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
    "MockConnectionProvider",
    "TcpDeviceConnection",
    "TcpConnectionProvider",
    "DeviceSession",
]
