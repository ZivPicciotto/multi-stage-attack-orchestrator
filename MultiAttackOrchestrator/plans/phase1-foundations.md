# Phase 1 — Foundations (pure types)

**Goal:** define every value type and contract the rest of the framework is built from. No I/O,
no control flow, no device — just data and shapes. If this phase is right, later phases are mostly
wiring.

**Depends on:** nothing. **Unlocks:** everything.

**Files:** `orchestrator/models/{phases,device,context,attack,results,extraction}.py`

---

## `phases.py` — OrchestrationPhase

```python
class OrchestrationPhase(Enum):
    CONNECTING       = "connecting"
    GATHERING_INFO   = "gathering_info"
    RESOLVING_ATTACKS = "resolving_attacks"
    RUNNING_ATTACK   = "running_attack"
    EXTRACTING_DATA  = "extracting_data"
    DONE             = "done"
    FAILED           = "failed"
```

Used for logging the run's progression and as `final_phase` on `MultiAttackResult`, so a failure
report says *how far we got* ("failed at RESOLVING_ATTACKS" = no compatible attack; "failed at
EXTRACTING_DATA" = we got in but the pull broke).

## `device.py` — DeviceInfo, IOSVersion, DeviceCompatibilityReqs

```python
@dataclass(frozen=True, order=True)
class IOSVersion:
    major: int; minor: int = 0; patch: int = 0
    @classmethod
    def parse(cls, s: str) -> "IOSVersion": ...      # "15.4.1" -> IOSVersion(15,4,1)

@dataclass(frozen=True)
class DeviceInfo:
    model: str            # e.g. "iPhone11,8"
    ios_version: IOSVersion
    battery_level: int    # 0..100
    # optional realistic extensions, documented but not required:
    # usb_restricted: bool = False   # data-over-USB disabled after lock timeout

@dataclass(frozen=True)
class DeviceCompatibilityReqs:
    min_ios: IOSVersion | None = None
    max_ios: IOSVersion | None = None
    supported_models: frozenset[str] | None = None   # None = any model
    min_battery: int = 0
    def matches(self, info: DeviceInfo) -> bool: ...
    def reasons_incompatible(self, info: DeviceInfo) -> list[str]: ...  # for reporting/logging
```

**Decision — version as a comparable value type, not a string.** `order=True` gives us `<`/`>=`
for free, so `matches` is a clean set of comparisons. We avoid a third-party dep
(`packaging.version`) to keep Part 1 dependency-free; a hand-rolled `IOSVersion` is a few lines and
fully testable. `matches()` returns the bool the resolver needs; `reasons_incompatible()` exists
so logs/reports can explain *why* an attack was filtered out (nice for debugging, not required for
the result object).

**Which fields "matter":** model (ties to the hardware bug an attack targets), iOS version
(patches close bugs), battery (long attacks need power). Everything else is out of scope; the
prompt explicitly leaves "what else matters" to us and we keep it lean.

## `context.py` — SingleAttackSharedContext

```python
class SingleAttackSharedContext:
    """Shared, mutable scratchpad threaded through one chain attempt.
    Stages write named outputs; later stages read them."""
    def set(self, key: str, value: object) -> None: ...
    def get(self, key: str, default=None) -> object: ...
    def __contains__(self, key: str) -> bool: ...
```

**Decision — one shared context, not typed adjacent-stage handoffs.** A stage may need output from
*two* stages back, not just the previous one; a single growing context handles that naturally and
is the idiomatic pattern in dynamically-typed pipelines (CI artifacts, Airflow XComs). It is
mutable and lives for exactly one chain attempt — on a crash-restart the orchestrator creates a
fresh one, because the device was reset and any accumulated tokens are stale.

## `attack.py` — SingleStage, Attack

```python
@dataclass
class SingleStage:
    name: str
    stage_id: str                 # command sent to the device to run this step
    success_probability: float    # ATTACKER'S ESTIMATE — used only for ranking
    max_retries: int = 0          # ADDITIONAL in-place attempts after the first, on a clean failure

    def attempt(self, connection, context: SingleAttackSharedContext) -> "StageResult":
        result = connection.run_stage(self.stage_id)    # device decides reality (incl. result.crashed)
        if result.succeeded and result.payload is not None:
            context.set(self.name, result.payload)      # stash payload for later stages
        return result
        # ConnectionLostError is NOT caught here — it propagates to the orchestrator

@dataclass(frozen=True)
class Attack:
    id: str
    stages: tuple[SingleStage, ...]
    requirements: DeviceCompatibilityReqs
    max_restarts: int = 1          # full-chain restarts allowed (cost-of-failure knob)
    description: str = ""
    def __post_init__(self):
        if not self.stages:        # a zero-stage attack would rank as prob 1.0 (math.prod(()))
            raise ValueError(f"attack {self.id!r} must have at least one stage")
    @property
    def overall_probability(self) -> float:   # product of stage probabilities
        return math.prod(s.success_probability for s in self.stages)
```

**Note — there is no `crashes_on_failure` on the stage.** Whether a failure crashes the device is
the *device's* verdict (`StageResult.crashed`), not fixed stage metadata — see the results section
and the "one verdict type" decision below.

**Decision — `SingleStage` is a concrete class, not an ABC.** The common stage is pure metadata +
a generic `attempt()` that sends one command and maps the verdict. Making it abstract would force
a subclass per stage for no benefit ("dumb stages"). Special stages that need bespoke logic
subclass and override `attempt()`. This keeps the polymorphism in *data* (the stage list), where
it belongs.

**Decision — `attempt()` lets `ConnectionLostError` propagate.** Catching it here would blur the
stage/orchestrator split. The stage handles only the *logical* verdicts the device returns
(success / clean failure / crash — passing the `StageResult` straight through after stashing any
payload); the orchestrator owns the transport-fault reaction.

## `results.py` — the result vocabulary

```python
@dataclass(frozen=True)
class StageResult:       # one stage attempt's verdict — returned by the connection, passed through by the stage
    succeeded: bool
    payload: bytes | None = None   # data the device handed back on success (-> SingleAttackSharedContext)
    reason: str | None = None      # human-readable explanation on failure
    crashed: bool = False          # a failure that also left the device unusable (panic / reboot)
    @classmethod
    def ok(cls, payload=None): ...      # succeeded
    @classmethod
    def fail(cls, reason): ...          # clean failure — device intact, retryable in place
    @classmethod
    def crash(cls, reason): ...         # failure that crashed the device — chain must restart

@dataclass(frozen=True)
class AttackResult:
    attack_id: str
    status: AttackResultType            # SUCCESS | FAILED | SKIPPED
    failed_stage: str | None = None # set on FAILED
    reason: str | None = None
    restarts_used: int = 0
    @property
    def succeeded(self) -> bool: return self.status is AttackResultType.SUCCESS

@dataclass(frozen=True)
class FileResult:
    path: str
    succeeded: bool
    data: bytes | None = None
    error: str | None = None

@dataclass(frozen=True)
class ExtractionOutcome:
    mode: "ExtractionMode"
    files: tuple[FileResult, ...] = ()
    error: str | None = None        # set if the session died mid-extraction
    @property
    def succeeded(self) -> bool: ...   # unlock: True; single/multi/all: all files ok
    @property
    def partial(self) -> bool: ...     # some succeeded, some didn't

@dataclass(frozen=True)
class MultiAttackResult:
    requested_mode: "ExtractionMode"
    final_phase: OrchestrationPhase
    succeeded: bool
    winning_attack: str | None
    attempts: tuple[AttackResult, ...]   # every attack tried, in order, with where each failed
    extraction: ExtractionOutcome | None
    error: str | None = None
```

**Decision — one verdict type (`StageResult`), not two.** The connection returns it from
`run_stage()`, and the stage passes it straight through (after stashing any payload into the
context). We considered a separate wire-level `StageOutcome` vs. framework-level `StageResult`
split, but at this scale it's ceremony — a single type carrying `succeeded` + `payload` + `reason`
+ `crashed` serves both. `StageResult` deliberately does **not** carry a stage name: the orchestrator always
knows which stage it invoked (it holds the `SingleStage`), so it reads `stage.name` for
`AttackResult.failed_stage` rather than duplicating it in every result.

**Decision — per-file extraction results.** `all_files`/`multi_files` return a tuple of
`FileResult`, never one boolean, so "got 8 of 10, here's the 2 that failed and why" survives into
the report. `ExtractionOutcome.succeeded`/`partial` collapse that when a caller just wants a
summary.

## `extraction.py` — ExtractionMode, ExtractionRequest

```python
class ExtractionMode(Enum):
    UNLOCK = "unlock"; SINGLE_FILE = "single_file"
    MULTI_FILES = "multi_files"; ALL_FILES = "all_files"

@dataclass(frozen=True)
class ExtractionRequest:
    mode: ExtractionMode
    paths: tuple[str, ...] = ()
    def __post_init__(self):
        # boundary validation: single -> exactly 1 path; multi -> >=1; unlock/all -> 0
        ...
```

**Decision — validate at the boundary.** `ExtractionRequest` is user input, so `__post_init__`
enforces the mode/paths invariant and raises `ValueError` early, rather than letting a bad request
fail deep inside the extractor.

## Tests (`test_models.py`)

- `IOSVersion.parse` round-trips; ordering (`15.4 < 15.4.1 < 16.0`).
- `DeviceCompatibilityReqs.matches`: below `min_ios`, above `max_ios`, wrong model, low battery,
  and the all-pass case; `reasons_incompatible` lists the right reasons.
- `Attack.overall_probability` equals the product; single-stage case; `__post_init__` rejects a
  zero-stage attack (guards the `math.prod(()) == 1.0` trap).
- `StageResult.ok`/`fail`/`crash` set `succeeded`/`crashed` correctly (clean vs crashing failure).
- `ExtractionRequest.__post_init__` rejects single-with-0-paths, unlock-with-paths, etc.
- `ExtractionOutcome.succeeded`/`partial` across all/none/some-succeeded, incl. all-failed multi
  (`succeeded=False, partial=False`) and empty `all_files` (`succeeded=True`).
