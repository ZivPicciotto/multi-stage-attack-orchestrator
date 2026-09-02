# Phase 2 — Device connection layer

**Goal:** define the one seam between the framework and "the device," and back it with a
scriptable in-memory mock. This is the interface Part 2's TCP client must satisfy, so its shape is
chosen with the wire protocol already in mind.

**Depends on:** phase 1. **Unlocks:** phases 3–6 (everything that touches a device).

**Files:** `orchestrator/connection/{base,mock,provider,session}.py`,
`orchestrator/device_info.py`

---

## `base.py` — the contract + error hierarchy

```python
class DeviceConnection(Protocol):
    # Every I/O method may raise ConnectionLostError (incl. ConnectionTimeout). A per-connection
    # timeout is set at construction (see provider) so a half-open socket in Part 2 surfaces as a
    # timeout rather than hanging forever — the framework never blocks unbounded.
    def get_device_info(self) -> DeviceInfo: ...
    def run_stage(self, stage_id: str) -> StageResult: ...    # StageResult.crashed carries crash verdict
    def list_files(self) -> list[str]: ...
    def read_file(self, path: str) -> bytes: ...              # may also raise RemoteFileError
    def close(self) -> None: ...

class DeviceError(Exception): ...
class ConnectionLostError(DeviceError): ...   # transport died (crash / unplug / drop)
class ConnectionTimeout(ConnectionLostError): ...  # I/O exceeded the deadline — treated as a drop
class RemoteFileError(DeviceError): ...        # file missing or access denied (NOT fatal to session)
class ProtocolError(DeviceError): ...          # malformed response (framing/parse bug) — fatal to the run
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

**Decision — reserve a timeout now, even though the mock never needs one.** Every I/O method may
raise `ConnectionLostError`, and `ConnectionTimeout` is a subtype of it, so the orchestrator's
existing "reconnect + restart on connection loss" path already covers a timeout with no new
handling. Baking the timeout into the contract in Part 1 is what keeps the promise that the seam
doesn't change for Part 2 — where a half-open TCP socket would otherwise hang the whole framework
and `ConnectionLostError` would never fire on its own.

**Decision — four error classes, four meanings.** `ConnectionLostError`/`ConnectionTimeout` → the
session is dead, reconnect + restart. `RemoteFileError` → one file failed, keep going.
`ProtocolError` → the two sides disagree on the wire format; that's a bug no retry fixes, so it's
**fatal to the whole run** and caught only at the top orchestrator (phase 6). This is what lets the
orchestrator react differently to "exploit missed" vs "socket died" vs "we're desynced."

## `mock.py` — InMemoryDeviceConnection (the Part 1 stand-in)

```python
@dataclass
class DeviceState:                 # mutable — the mock models a real device that changes over time
    model: str
    ios_version: IOSVersion
    battery_level: int
    alive: bool = True             # flips False on a drop/crash; further calls raise ConnectionLostError
    unlocked: bool = False         # set True when a chain completes
    filesystem: dict[str, bytes] = field(default_factory=dict)

class InMemoryDeviceConnection:            # structurally a DeviceConnection
    def __init__(self, state: DeviceState, behavior: "Behavior", timeout: float | None = None): ...
```

**The mock holds mutable `DeviceState`, not a frozen snapshot.** This matters: several headline
tests depend on state *changing* — `get_device_info()` must be able to report a lower battery on a
re-check (phase 6's state-drift test), and a crash/drop must actually flip `alive` so the next call
fails. A static snapshot would make those tests fiction. `get_device_info()` reads the *current*
state each call; `run_stage`/`read_file` may mutate it (drain battery, set `unlocked`, kill the
device). A `DeviceState` can be shared across the sequence of connections a provider hands out, so
state persists across a reconnect the way a real device would.

`Behavior` decides each `run_stage` verdict and can mutate the state, in two modes:

- **Scripted (default for tests):** a per-`stage_id` queue of outcomes, e.g.
  `{"bootrom": [OK], "kernel": [FAIL, CRASH], "escalate": [DROP]}`. `run_stage` pops the next:
  `OK`→`StageResult.ok(payload)`, `FAIL`→`StageResult.fail(...)` (device intact), `CRASH`→
  `StageResult.crash(...)` **and sets `alive=False`**, `DROP`→raises `ConnectionLostError`. Fully
  deterministic — every failure test asserts an exact path, no luck involved.
- **Probabilistic (for the demo):** per-stage success/crash/drop probabilities driven by a
  **seeded** `random.Random`, so even "random" runs are reproducible.

`read_file` returns bytes from `state.filesystem` or raises `RemoteFileError`; it can also be
scripted to drop mid-extraction (to exercise the phase-5 partial case). Once `alive` is False —
after a `CRASH` or `DROP` — every further call raises `ConnectionLostError`; a real dead socket
doesn't recover, and neither should the mock.

**Why the mock mirrors the simulator's job:** in Part 1 the mock *is* the authority on outcomes
(success / clean-fail / crash / drop) and on device state, exactly as the C simulator will be in
Part 2. Same interface, same failure vocabulary — so the orchestration code can't tell them apart,
which is the whole point.

## `provider.py` — DeviceConnectionProvider

```python
class DeviceConnectionProvider(Protocol):
    def connect(self, target: ConnectionTarget) -> DeviceConnection: ...

class MockConnectionProvider:   # Part 1
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

**Decision — keep it, but it earns its place via re-checking, not enrichment.** A fair critique is
that this looks like a pointless one-line wrapper. Its real job is that it's the **single point
that re-reads live device state before every attack attempt** (phase 6) — and because the mock now
holds *mutable* state, that re-read genuinely returns different data as battery drains or the device
reboots, which is what drives the state-drift skip logic. Centralizing "what attributes the
framework needs and when we re-read them" in one component (rather than scattering
`get_device_info()` calls) is what makes that behavior testable and gives future enrichment
(deriving chip generation from model, combining multiple device queries) one obvious home. If it
never grew past this, inlining it would be reasonable — we keep it because re-check-before-attempt
is a real, tested responsibility, not a hypothetical one.

## Tests (`test_connection.py`)

- Scripted mock: `run_stage` returns queued outcomes in order; `DROP` raises `ConnectionLostError`
  and the connection stays dead afterward.
- `read_file` returns vFS bytes; missing path raises `RemoteFileError`; scripted mid-read drop
  raises `ConnectionLostError`.
- `DeviceSession`: context manager opens/closes; `reconnect()` swaps to the next behavior and
  bumps `reconnect_count`.
- `MockConnectionProvider` hands out independent connections per `connect()`.
- `DeviceInfoProvider.get_info` returns the mock's `DeviceInfo`.
