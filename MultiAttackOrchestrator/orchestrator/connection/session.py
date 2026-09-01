"""Owns the current connection to one target and knows how to get a fresh one."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orchestrator.connection.base import DeviceConnection, DeviceError
from orchestrator.connection.provider import DeviceConnectionProvider

if TYPE_CHECKING:
    from orchestrator.config import ConnectionTarget

logger = logging.getLogger(__name__)


class DeviceSession:
    """Resolves the tension between 'the top orchestrator owns the connection lifecycle' and 'a
    crash needs a new connection': this is the one place both live. The top orchestrator opens
    and closes the session; a sub-orchestrator calls reconnect() on crash/drop and reads
    `.connection` for each stage; extraction reads the same live connection after a win.
    """

    def __init__(self, provider: DeviceConnectionProvider, target: "ConnectionTarget") -> None:
        self._provider = provider
        self._target = target
        self._connection: DeviceConnection | None = None
        self.reconnect_count = 0

    def __enter__(self) -> "DeviceSession":
        self._connection = self._provider.connect(self._target)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._connection is not None:
            self._connection.close()

    @property
    def connection(self) -> DeviceConnection:
        if self._connection is None:
            raise RuntimeError("DeviceSession used outside its context manager")
        return self._connection

    def reconnect(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except DeviceError:
                pass  # already dead — closing a dead connection is not itself an error
        self._connection = self._provider.connect(self._target)
        self.reconnect_count += 1
        logger.info("session: reconnected (attempt #%d)", self.reconnect_count)
