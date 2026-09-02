# Phase F — Scenario files and the cross-transport demo

**Goal:** prove the seam actually held — the same 8 scenarios from Part 1's `demo.py`, run against
the real C simulator over a real socket, telling the same story as they did against the in-memory
mock. This is the closing argument for the whole Part 1/Part 2 split.

**Depends on:** phases D, E. **Unlocks:** Part 3 (integration tests reuse these scenario files and
`TcpConnectionProvider` directly).

**Files:** `Simulator/scenarios/*.json`, a `--tcp` mode (or a parallel `demo_tcp.py`) in
`MultiAttackOrchestrator/orchestrator/demo.py`, README updates (folded in here rather than as a
separate phase, matching Part 1's precedent).

---

## Scenario files

One JSON file per Part 1 demo scenario that involves scripted behavior (the pure-default happy
path needs no file at all — an empty `{"device": {...}, "filesystem": {}}` suffices):

| File | Mirrors | Key scripted content |
|---|---|---|
| `01_happy_path.json` | `scenario_happy_path` | no stage scripting — everything defaults to OK |
| `02_retry_then_succeed.json` | `scenario_retry_then_succeed` | `bootrom`: `[fail, ok]` |
| `03_crash_then_restart.json` | `scenario_crash_then_restart` | `kernel_rw`: `[crash, ok]`; iOS 15.0 device |
| `04_connection_drop_then_restart.json` | `scenario_connection_drop_then_restart` | `payload`: `[drop, ok]` |
| `05_fallback_after_failure.json` | `scenario_fallback_after_failure` | `payload`: `[fail]` |
| `06_state_drift_skip.json` | `scenario_state_drift_skip` | `payload`: `[fail]` + `battery_drain` on dfu/bootrom/payload |
| `07_all_attacks_fail.json` | `scenario_all_attacks_fail` | `payload`/`kernel_rw`/`bruteforce` each `[fail]` |

**Decision — these files are a direct translation, not a reinterpretation.** Every number
(battery-drain amounts, which stage fails) is copied from `demo.py`'s existing Python scenario
functions, not redesigned. If the translation is faithful, running scenario N through the simulator
should produce a `MultiAttackResult` matching scenario N's mock-backed run field-for-field
(`succeeded`, `winning_attack`, each attempt's `status`/`failed_stage`/`reason`).

## Cross-transport demo mode

`demo.py` gets a `--tcp <port>` flag (or a thin `demo_tcp.py` that imports and reuses `demo.py`'s
scenario functions with the provider swapped):

```python
def main() -> None:
    configure_logging()
    args = parse_args()
    if args.tcp_port:
        simulator = launch_simulator_subprocess(args.tcp_port, SCENARIOS_DIR)
        provider_factory = lambda scenario_file: TcpConnectionProvider()  # + point at the right scenario file per run
    else:
        provider_factory = lambda _: MockConnectionProvider(...)
    ...
```

The exact plumbing (one simulator subprocess per scenario, restarted with a different scenario
file each time vs. one long-lived process serving all scenarios sequentially) is a phase F
implementation detail to settle when writing this — the simpler option is one subprocess launch
per scenario, since the simulator's scenario file is fixed at startup via `argv`, and Part 1's
demo already treats each scenario as an independent, freshly-constructed run.

## What "the seam held" means concretely

Running `demo.py` and `demo.py --tcp <port>` back to back and diffing their `RESULT:`/`attempt:`
log lines (ignoring timestamps) should show **identical** `succeeded`, `winning_attack`,
`final_phase`, and per-attempt `status`/`failed_stage`/`restarts_used` for every scenario. Any
divergence means either the wire protocol doesn't actually implement the `DeviceConnection`
contract faithfully, or a scenario JSON file doesn't match its Python counterpart — both are bugs
to fix before calling Part 2 done, not before Part 3 starts.

## README updates (folded into this phase)

- The wire protocol table from the overview (condensed to essentials).
- Build: `cd Simulator && make`.
- Run standalone: `./simulator <port> scenarios/03_crash_then_restart.json`.
- Point Python at it: `TcpConnectionProvider()` + `ConnectionTarget(host, port)` in place of
  `MockConnectionProvider` — one line, per phase E.
- A one-paragraph note on the design choices worth defending in an exercise like this: config-driven
  simulator (no compiled-in stage IDs), the generated shared protocol module, crash-then-close
  semantics, and single-threaded accept loop — each with the one-line "why" already captured in
  the phase docs.

## Tests

Phase F itself doesn't add new pytest assertions beyond what phase E already covers — its
"test" is the diff described above, run manually or as a small script that runs both transports
and asserts equality on the comparable fields. This is deliberately a looser, narrative-level check
rather than another layer of unit tests: phases B through E already have granular coverage: this
phase is about confirming the *whole* thing composes correctly end to end.
