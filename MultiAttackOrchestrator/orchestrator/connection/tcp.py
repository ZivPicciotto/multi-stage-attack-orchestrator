"""The real transport: talks to the C simulator over TCP. Structurally a DeviceConnection —
same contract as InMemoryDeviceConnection — so nothing above the seam changes to use it.
"""

from __future__ import annotations

import socket
import struct
from typing import TYPE_CHECKING

from orchestrator.connection.base import (
    ConnectionLostError,
    ConnectionTimeout,
    ProtocolError,
    RemoteFileError,
)
from orchestrator.models.device import DeviceInfo, IOSVersion
from orchestrator.models.results import StageResult
from orchestrator.shared_protocol import (
    FRAME_BYTE_ORDER,
    RequestType,
    ResponseType,
)

if TYPE_CHECKING:
    from orchestrator.config import ConnectionTarget

# Derived from the generated FRAME_BYTE_ORDER constant rather than hardcoded, so a spec.json
# change would actually be felt here instead of silently drifting.
_HEADER_FORMAT = ("!BI" if FRAME_BYTE_ORDER == "big" else "<BI")
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)

DEFAULT_TIMEOUT = 5.0


class TcpDeviceConnection:
    """Structurally a DeviceConnection. One TCP socket, one request-response pair at a time."""

    def __init__(self, sock: socket.socket, timeout: float | None = DEFAULT_TIMEOUT) -> None:
        self._sock = sock
        self._sock.settimeout(timeout)

    def get_device_info(self) -> DeviceInfo:
        self._send(RequestType.GET_INFO, b"")
        rtype, payload = self._recv()
        self._expect(rtype, ResponseType.OK)
        model, version, battery = payload.decode("utf-8").split("|")
        return DeviceInfo(model, IOSVersion.parse(version), int(battery))

    def run_stage(self, stage_id: str) -> StageResult:
        self._send(RequestType.RUN_STAGE, stage_id.encode("utf-8"))
        rtype, payload = self._recv()
        if rtype is ResponseType.OK:
            return StageResult.ok(payload or None)
        if rtype is ResponseType.FAIL:
            return StageResult.fail(payload.decode("utf-8"))
        if rtype is ResponseType.CRASH:
            return StageResult.crash(payload.decode("utf-8"))
        raise ProtocolError(f"unexpected response type for RUN_STAGE: {rtype!r}")

    def list_files(self) -> list[str]:
        self._send(RequestType.LIST_FILES, b"")
        rtype, payload = self._recv()
        self._expect(rtype, ResponseType.OK)
        return payload.decode("utf-8").splitlines() if payload else []

    def read_file(self, path: str) -> bytes:
        self._send(RequestType.READ_FILE, path.encode("utf-8"))
        try:
            rtype, payload = self._recv()
        except ConnectionLostError as e:
            # _recv doesn't know which request it was answering; add that context here, at the
            # one call site that does — matching the mock's read_file message exactly.
            raise ConnectionLostError(f"connection dropped while reading {path!r}") from e
        if rtype is ResponseType.FILE_ERROR:
            raise RemoteFileError(payload.decode("utf-8"))
        self._expect(rtype, ResponseType.OK)
        return payload

    def close(self) -> None:
        self._sock.close()

    def _expect(self, got: ResponseType, want: ResponseType) -> None:
        if got is not want:
            raise ProtocolError(f"expected {want!r}, got {got!r}")

    def _send(self, req_type: RequestType, payload: bytes) -> None:
        header = struct.pack(_HEADER_FORMAT, req_type, len(payload))
        try:
            self._sock.sendall(header + payload)
        except TimeoutError as e:
            raise ConnectionTimeout(str(e)) from e
        except OSError as e:
            raise ConnectionLostError(str(e)) from e

    def _recv(self) -> tuple[ResponseType, bytes]:
        header = self._recv_exact(_HEADER_SIZE)
        if header == b"":
            # No bytes at all before the peer closed — a scripted DROP, or the socket close
            # that follows a RES_CRASH frame on a *later* call on this same connection.
            raise ConnectionLostError("connection closed by peer")
        rtype_byte, length = struct.unpack(_HEADER_FORMAT, header)
        payload = self._recv_exact(length) if length else b""
        try:
            rtype = ResponseType(rtype_byte)
        except ValueError:
            raise ProtocolError(f"unrecognized response type byte: {rtype_byte:#x}") from None
        if rtype is ResponseType.PROTOCOL_ERROR:
            raise ProtocolError(payload.decode("utf-8", errors="replace"))
        return rtype, payload

    def _recv_exact(self, n: int) -> bytes:
        """Loops over recv() until n bytes arrive or the peer closes — the client-side mirror
        of frame_read's read loop in the C server. A short recv() is not EOF."""
        chunks = bytearray()
        while len(chunks) < n:
            try:
                chunk = self._sock.recv(n - len(chunks))
            except TimeoutError as e:
                raise ConnectionTimeout(str(e)) from e
            except OSError as e:
                raise ConnectionLostError(str(e)) from e
            if chunk == b"":
                if len(chunks) == 0:
                    return b""
                raise ConnectionLostError(
                    f"connection closed mid-frame ({len(chunks)}/{n} bytes)"
                )
            chunks += chunk
        return bytes(chunks)


class TcpConnectionProvider:
    """Satisfies DeviceConnectionProvider. This is the entire swap from the mock: everything
    above the DeviceConnection line — DeviceSession, MultiAttackOrchestrator,
    SingleAttackOrchestrator, AttackResolver, DataExtractor — is unchanged.
    """

    def __init__(self, timeout: float | None = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def connect(self, target: "ConnectionTarget") -> TcpDeviceConnection:
        try:
            sock = socket.create_connection((target.host, target.port), timeout=self._timeout)
        except TimeoutError as e:
            raise ConnectionTimeout(str(e)) from e
        except OSError as e:
            raise ConnectionLostError(str(e)) from e
        return TcpDeviceConnection(sock, self._timeout)
