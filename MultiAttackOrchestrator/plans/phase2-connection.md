# Phase 2 — Device connection layer

**Goal:** define the one seam between the framework and "the device," and back it with a
scriptable in-memory fake. This is the interface Part 2's TCP client must satisfy, so its shape is
chosen with the wire protocol already in mind.

**Depends on:** phase 1. **Unlocks:** phases 3–6 (everything that touches a device).

**Files:** `orchestrator/connection/{base,fake,provider,session}.py`,
`orchestrator/device_info.py`

---

## `base.py` — the contract + error hierarchy

```python
class DeviceConnection(Protocol):
    def get_device_info(self) -> DeviceInfo: ...
    def run_stage(self, stage_id: str) -> StageResult: ...    # may raise ConnectionLostError
    def list_files(self) -> list[str]: ...
    def read_file(self, path: str) -> bytes: ...              # may raise RemoteFileError / ConnectionLostError
    def close(self) -> None: ...

class DeviceError(Exception): ...
class ConnectionLostError(DeviceError): ...   # transport died (crash / unplug / drop)
class RemoteFileError(DeviceError): ...        # file missing or access denied (NOT fatal to session)
class ProtocolError(DeviceError): ...          # malformed response (mostly a Part 2 concern)
```

**Decision — `Protocol`, not `ABC`.** The Part 2 TCP client conforms structurally, without
importing or subclassing anything from the framework. That's the right coupling for a component
that lives on the far side of a process boundary. (`SingleStage`, by contrast, is a concrete base
in the same package, so a plain class is fine there.)

**Decision — the method set is the protocol surface.** These five methods are exactly what the
wire protocol in Part 2 must support: `GET_INFO`, `RUN_STAGE`, `LIST_FILES`, `READ_FILE`, plus
teardown. Designing the Python interface first means the C protocol falls out of it rather than
being reverse-engineered later. `list_files` is what makes `all_files` extraction honest — the
client never hardcodes what's on the device.

**Decision — three error classes, three meanings.** `ConnectionLostError` → the session is dead,
reconnect. `RemoteFileError` → one file failed, keep going. `ProtocolError` → the two sides
disagree on the format (framing bug); surfaced loudly. This is what lets the orchestrator react
differently to "exploit missed" vs "socket died."

## `fake.py` — InMemoryDeviceConnection (the Part 1 stand-in)

```python
class InMemoryDeviceConnection:            # structurally a DeviceConnection
    def __init__(self, info: DeviceInfo,
                 filesystem: dict[str, bytes],
                 behavior: "Behavior"): ...
```

`Behavior` controls stage outcomes and drops, in two modes:

- **Scripted (default for tests):** a per-`stage_id` queue of outcomes, e.g.
  `{"bootrom": [OK], "kernel": [FAIL, OK], "escalate": [DROP]}`. `run_stage` pops the next one;
  `DROP` raises `ConnectionLostError`. Fully deterministic — every failure test asserts an exact
  path, no luck involved.
- **Probabilistic (for the demo):** each stage has a success prob + drop prob, driven by a
  **seeded** `random.Random`, so even "random" runs are reproducible.

`read_file` returns bytes from the virtual filesystem or raises `RemoteFileError`; it can also be
scripted to drop mid-extraction (to exercise the phase-5 partial case). After a `DROP`, the
instance marks itself dead and every further call raises `ConnectionLostError` — a real dropped
socket doesn't recover, and neither should the fake.

**Why the fake mirrors the simulator's job:** in Part 1 the fake *is* the authority on outcomes,
exactly as the C simulator will be in Part 2. Same interface, same failure vocabulary — so the
orchestration code can't tell them apart, which is the whole point.

## `provider.py` — DeviceConnectionProvider

```python
class DeviceConnectionProvider(Protocol):
    def connect(self, target: ConnectionTarget) -> DeviceConnection: ...

class FakeConnectionProvider:   # Part 1
    """Hands out fresh InMemoryDeviceConnections. Can be seeded with a *sequence* of behaviors
    so a reconnect yields a differently-behaving connection (e.g. first drops on stage 3,
    second succeeds) — required to test crash-restart."""
```

**Decision — the provider is the swap point.** Part 2 adds `TcpConnectionProvider` returning a
`TcpDeviceConnection`; nothing else changes. Each `connect()` returns a *fresh* connection, which
is what a crash-restart needs. Scanning for devices was deliberately cut — we connect to one
target.

## `session.py` — DeviceSession

```python
class DeviceSession:
    """Owns the current connection and knows how to get a fresh one. Resolves the tension
    between 'the top orchestrator owns the lifecycle' and 'a crash needs a new connection'."""
    def __init__(self, provider, target): ...
    def __enter__(self) -> "DeviceSession": ...   # opens the first connection
    def __exit__(self, *exc): ...                 # closes on the way out
    @property
    def connection(self) -> DeviceConnection: ...
    def reconnect(self) -> None: ...              # close current, open fresh; counts reconnects
    reconnect_count: int
```

**Decision — introduce a session object.** Without it, connection ownership gets muddy: the
top orchestrator "owns the lifecycle," yet a crash-restart deep inside the single-attack
orchestrator needs to replace the connection, and the *winning* connection must survive to feed
extraction. `DeviceSession` localizes all of that: the top orchestrator opens/closes it (context
manager), the single-attack orchestrator calls `reconnect()` on crash/drop and reads
`session.connection` for each stage, and extraction reads the same live connection after success.
One owner, one place reconnection logic lives.

## `device_info.py` — DeviceInfoProvider

```python
class DeviceInfoProvider:
    def get_info(self, connection: DeviceConnection) -> DeviceInfo:
        return connection.get_device_info()   # thin today; the seam for enrichment later
```

**Decision — keep it, thin.** It's the single place that answers "what device attributes does the
framework need, and how do we obtain them." Today it delegates to one call; it's where re-checking
state routes through, and where future enrichment (deriving chip generation from model, combining
multiple device queries) would live. A named component now beats threading raw `get_device_info()`
calls everywhere later.

## Tests (`test_connection.py`)

- Scripted fake: `run_stage` returns queued outcomes in order; `DROP` raises `ConnectionLostError`
  and the connection stays dead afterward.
- `read_file` returns vFS bytes; missing path raises `RemoteFileError`; scripted mid-read drop
  raises `ConnectionLostError`.
- `DeviceSession`: context manager opens/closes; `reconnect()` swaps to the next behavior and
  bumps `reconnect_count`.
- `FakeConnectionProvider` hands out independent connections per `connect()`.
- `DeviceInfoProvider.get_info` returns the fake's `DeviceInfo`.
