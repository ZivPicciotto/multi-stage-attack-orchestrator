# Phase E — Python TCP client

**Goal:** `TcpDeviceConnection` and `TcpConnectionProvider` — the only new code the Python
framework needs. This is the payoff of the whole Part 1 design: everything above the
`DeviceConnection` line is untouched.

**Depends on:** phases A (opcodes), D (a real server to talk to). **Unlocks:** phase F.

**Files:** `MultiAttackOrchestrator/orchestrator/connection/tcp.py`.

---

## `TcpDeviceConnection`

```python
class TcpDeviceConnection:  # structurally a DeviceConnection — same contract as InMemoryDeviceConnection
    def __init__(self, sock: socket.socket, timeout: float | None = 5.0) -> None:
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
        raise ProtocolError(f"unexpected response type for RUN_STAGE: {rtype}")

    def list_files(self) -> list[str]:
        self._send(RequestType.LIST_FILES, b"")
        rtype, payload = self._recv()
        self._expect(rtype, ResponseType.OK)
        return payload.decode("utf-8").splitlines() if payload else []

    def read_file(self, path: str) -> bytes:
        self._send(RequestType.READ_FILE, path.encode("utf-8"))
        rtype, payload = self._recv()
        if rtype is ResponseType.FILE_ERROR:
            raise RemoteFileError(payload.decode("utf-8"))
        self._expect(rtype, ResponseType.OK)
        return payload

    def close(self) -> None:
        self._sock.close()
```

**Decision — `run_stage` maps wire responses to `StageResult` exactly like the mock does.** The
mock's `run_stage` returns `StageResult.ok/fail/crash` directly; here the same three cases are
reconstructed from `RES_OK`/`RES_FAIL`/`RES_CRASH`. `SingleAttackOrchestrator` (Part 1, phase 4)
never sees the difference — it only ever looks at `result.succeeded` / `result.crashed` /
`result.reason`, all of which are populated identically regardless of transport.

## `_send` / `_recv` — socket-to-exception translation

```python
def _send(self, req_type: RequestType, payload: bytes) -> None:
    header = struct.pack("!BI", req_type, len(payload))
    try:
        self._sock.sendall(header + payload)
    except TimeoutError as e:
        raise ConnectionTimeout(str(e)) from e
    except OSError as e:
        raise ConnectionLostError(str(e)) from e

def _recv(self) -> tuple[ResponseType, bytes]:
    header = self._recv_exact(5)
    if header == b"":
        raise ConnectionLostError("connection closed by peer")   # the DROP / crash-then-close case
    rtype_byte, length = struct.unpack("!BI", header)
    payload = self._recv_exact(length) if length else b""
    try:
        rtype = ResponseType(rtype_byte)
    except ValueError:
        raise ProtocolError(f"unrecognized response type byte: {rtype_byte:#x}") from None
    if rtype is ResponseType.PROTOCOL_ERROR:
        raise ProtocolError(payload.decode("utf-8", errors="replace"))
    return rtype, payload

def _recv_exact(self, n: int) -> bytes:
    """Loops over recv() until n bytes arrive or the peer closes — the client-side mirror of
    frame_read's read loop in the C server. A short recv() is not EOF."""
    chunks = bytearray()
    while len(chunks) < n:
        try:
            chunk = self._sock.recv(n - len(chunks))
        except TimeoutError as e:
            raise ConnectionTimeout(str(e)) from e
        except OSError as e:
            raise ConnectionLostError(str(e)) from e
        if chunk == b"":
            return b"" if len(chunks) == 0 else self._raise_truncated(len(chunks), n)
        chunks += chunk
    return bytes(chunks)

def _raise_truncated(self, got: int, expected: int):
    raise ConnectionLostError(f"connection closed mid-frame ({got}/{expected} bytes)")
```

**Decision — `_recv_exact` distinguishes "closed before any bytes" from "closed mid-frame."**
Both end up as `ConnectionLostError` (the orchestrator reacts to them identically — restart the
chain), but the two messages matter for debugging: a clean pre-frame close is an ordinary DROP or
post-CRASH close; a mid-frame close usually means something actually went wrong in the simulator
or the network, which is worth being able to tell apart when reading logs.

**Decision — an unrecognized response type byte, or an explicit `RES_PROTOCOL_ERROR`, both become
`ProtocolError`.** This is where "the two sides disagree on the wire format" is actually detected
on the Python side — not something the server has to get right about every conceivable malformed
input, just something the client notices when what comes back doesn't parse as a known response.

## `TcpConnectionProvider`

```python
class TcpConnectionProvider:  # satisfies DeviceConnectionProvider
    def connect(self, target: ConnectionTarget) -> DeviceConnection:
        sock = socket.create_connection((target.host, target.port), timeout=5.0)
        return TcpDeviceConnection(sock)
```

**This is the entire swap.** `DeviceSession`, `MultiAttackOrchestrator`,
`SingleAttackOrchestrator`, `AttackResolver`, `DataExtractor` — none of them import or reference
`TcpConnectionProvider` or `TcpDeviceConnection` by name; they only ever see the
`DeviceConnectionProvider`/`DeviceConnection` Protocol types. Passing a `TcpConnectionProvider`
into `MultiAttackOrchestrator(provider=...)` in place of a `MockConnectionProvider` is a one-line
change in whatever constructs the orchestrator — nothing inside `orchestrator/` changes at all.

## Tests

`tests/test_tcp_connection.py`, gated behind a pytest fixture that launches the real
`Simulator/simulator` binary as a subprocess (built via the `Makefile`) against a temp scenario
file, and tears it down after. Reuses the **exact same assertions** as Part 1's
`test_connection.py` — scripted retry, crash kills the connection, reconnect revives it, battery
drain, drop-on-read — but driving a `TcpDeviceConnection` against the real subprocess instead of
the in-memory mock. If this test file is structurally a near-duplicate of `test_connection.py` with
the fixture swapped, that's the proof the seam held; if it needs materially different assertions,
that's a signal something about the wire protocol doesn't actually match the `DeviceConnection`
contract and needs fixing before Part 3.
