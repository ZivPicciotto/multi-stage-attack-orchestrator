# Phase 3 — Attack model & resolution

**Goal:** provide a catalog of sample attacks and the logic that, given a device, returns the
viable attacks ranked best-first. This is the "which attack do we run?" decision from the prompt.

**Depends on:** phases 1–2. **Unlocks:** phase 6 (the top orchestrator consumes the ranked list).

**Files:** `orchestrator/attacks/catalog.py`, `orchestrator/resolver.py`

---

## `catalog.py` — sample attacks

A handful of `Attack` instances, deliberately overlapping so the selection logic has real work to
do (several viable for one device). Sketch:

```python
BOOTROM_CHAIN = Attack(
    id="bootrom-checkm8-style",
    description="Hardware bug; unpatchable, cheap to retry.",
    requirements=DeviceCompatibilityReqs(
        max_ios=IOSVersion(14, 8),
        supported_models=frozenset({"iPhone10,3", "iPhone10,6", "iPhone11,8"}),
        min_battery=10,
    ),
    max_restarts=3,                     # failing an attempt costs nothing → allow restarts
    stages=(
        SingleStage("DFU entry",       "dfu",     0.95, max_retries=2),
        SingleStage("Bootrom trigger", "bootrom", 0.80, max_retries=1),
        SingleStage("Payload upload",  "payload", 0.90),
    ),
)

KERNEL_CHAIN = Attack(
    id="kernel-exploit",
    description="Software bug; a failed attempt can panic the device.",
    requirements=DeviceCompatibilityReqs(
        min_ios=IOSVersion(14, 0), max_ios=IOSVersion(15, 4), min_battery=30,
    ),
    max_restarts=1,
    stages=(
        SingleStage("Info leak",  "leak",      0.85),
        SingleStage("Kernel R/W", "kernel_rw", 0.70),  # can panic — but that's the device's call
        SingleStage("Escalate",   "escalate",  0.90, max_retries=1),
    ),
)

PASSCODE_CHAIN = Attack(   # low overall prob, high cost of failure → ranks last; here for contrast
    id="passcode-bruteforce", ...
    max_restarts=0,        # burns the attempt counter; never restart
)

CATALOG: tuple[Attack, ...] = (BOOTROM_CHAIN, KERNEL_CHAIN, PASSCODE_CHAIN)
```

The catalog is plain data — no behavior — so it doubles as readable documentation of "what an
attack looks like." The metadata differences (retry budgets, `max_restarts`) are what make
phase-4's failure handling exercisable. Whether a stage like `kernel_rw` actually panics on a
failure isn't declared here — that's the device's verdict (`StageResult.crashed`), configured on
the mock / simulator (phase 2), not on the stage.

## `resolver.py` — AttackResolver

```python
class AttackResolver:
    def __init__(self, catalog: tuple[Attack, ...] = CATALOG): ...

    def resolve(self, info: DeviceInfo) -> list[Attack]:
        viable = [a for a in self.catalog if a.requirements.matches(info)]
        return sorted(
            viable,
            key=lambda a: (a.overall_probability, -len(a.stages), a.id),
            reverse=True,     # highest probability first
        )
```

**Decision — rank by overall probability, deterministic tie-breaks.** Primary key is the product
of stage probabilities (see overview). Ties break toward **fewer stages** (less to go wrong,
faster) then **id** (so ordering is stable and tests are deterministic — never rely on Python's
sort landing a particular way on equal keys by accident).

**Decision — filtering and ranking are one pass, but each half is independently testable.**
`matches` lives on the reqs object (phase 1), so compatibility can be unit-tested with no resolver
at all; the resolver's own tests focus on ordering and the empty case.

**What the resolver does *not* do:** it doesn't run anything, doesn't touch the connection, and
doesn't re-check state — that's the top orchestrator's job (which re-resolves/re-checks before
each attempt). The resolver is a pure function of `(device_info, catalog)`, which keeps it trivial
to reason about and test.

## Worked example

For an `iPhone11,8` on iOS 14.2 at 60% battery:
- `BOOTROM_CHAIN` matches (model in set, iOS ≤ 14.8, battery ≥ 10) → prob `0.95·0.80·0.90 = 0.684`.
- `KERNEL_CHAIN` matches (14.0 ≤ 14.2 ≤ 15.4, battery ≥ 30) → prob `0.85·0.70·0.90 = 0.536`.
- `PASSCODE_CHAIN` — assume lower product.
- Result order: `[BOOTROM_CHAIN, KERNEL_CHAIN, PASSCODE_CHAIN]`. The orchestrator tries them in
  that order, falling through on failure.

Same device at **20% battery**: `KERNEL_CHAIN` drops out (needs ≥30), `BOOTROM_CHAIN` still
viable. This is exactly the state-dependent selection the prompt asks for.

## Tests (`test_resolver.py`)

- Only compatible attacks returned (model/iOS/battery filters each exclude the right ones).
- Ordering is by descending overall probability; tie-break falls to fewer stages then id.
- No compatible attack → empty list (phase 6 turns this into a clean "no viable attack" result).
- Adding battery/ios boundary devices flips membership as expected (the 60% vs 20% example).
