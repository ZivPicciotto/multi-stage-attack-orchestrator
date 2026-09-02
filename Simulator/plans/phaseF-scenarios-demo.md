# Phase F — Scenario files and the cross-transport demo

**Status: done.** `demo.py`/`demo.py --tcp` produce byte-identical `RESULT:`/`attempt:`/extraction
lines across all 9 scenarios (67/67 comparable lines) — the seam held.

**Goal:** prove the seam actually held — the same demo scenarios from Part 1's `demo.py`, run
against the real C simulator over a real socket, telling the same story as they did against the
in-memory mock. This is the closing argument for the whole Part 1/Part 2 split.

**Depends on:** phases D, E. **Unlocks:** Part 3 (integration tests reuse these scenario files and
`TcpConnectionProvider` directly).

**Files:** `Simulator/scenarios/*.json`, a `--tcp` mode in
`MultiAttackOrchestrator/orchestrator/demo.py`, README updates (folded in here rather than as a
separate phase, matching Part 1's precedent).

---

## Scenario files

One JSON file per Part 1 demo scenario that involves scripted behavior (the pure-default happy
path needs no scripting — an empty `stages`/`battery_drain`/`drop_on_read` suffices). Scenario 8
(`scenario_context_dependency`, added to the catalog after this plan was first written) and the two
sub-cases inside scenarios 7 and 8 are included too, since a faithful translation has to cover the
whole demo, not just what existed when this table was drafted:

| File | Mirrors | Key scripted content |
|---|---|---|
| `01_happy_path.json` | `scenario_happy_path` | no stage scripting — everything defaults to OK |
| `02_retry_then_succeed.json` | `scenario_retry_then_succeed` | `bootrom`: `[fail, ok]` |
| `03_crash_then_restart.json` | `scenario_crash_then_restart` | `kernel_rw`: `[crash, ok]`; iOS 15.0 device |
| `04_connection_drop_then_restart.json` | `scenario_connection_drop_then_restart` | `payload`: `[drop, ok]` |
| `05_fallback_after_failure.json` | `scenario_fallback_after_failure` | `payload`: `[fail]` |
| `06_state_drift_skip.json` | `scenario_state_drift_skip` | `payload`: `[fail]` + `battery_drain` on dfu/bootrom/payload |
| `07_all_attacks_fail.json` | `scenario_all_attacks_fail` | `payload`/`kernel_rw`/`bruteforce` each `[fail]` |
| `08_extraction_modes.json` | `scenario_extraction_modes` (7a/7b/7c) | filesystem populated, no scripting |
| `08_extraction_modes_drop_on_read.json` | `scenario_extraction_modes` (7d) | same filesystem + `drop_on_read` on the photo path — kept separate from the file above so 7c's `all_files` pull (which touches every path, including the photo) isn't also affected |
| `09a_context_dependency_success.json` | `scenario_context_dependency` (8a) | `class_key_leak`: `[{outcome: ok, payload: "..."}]` |
| `09b_context_dependency_missing_payload.json` | `scenario_context_dependency` (8b) | no stage scripting — leak succeeds with no payload |

**Decision — these files are a direct translation, not a reinterpretation.** Every number
(battery-drain amounts, which stage fails) is copied from `demo.py`'s existing Python scenario
functions, not redesigned. If the translation is faithful, running scenario N through the simulator
should produce a `MultiAttackResult` matching scenario N's mock-backed run field-for-field
(`succeeded`, `winning_attack`, each attempt's `status`/`failed_stage`/`reason`).

**A real gap this surfaced:** `scenario_context_dependency`'s 8a case needs `RUN_STAGE`'s `RES_OK`
to carry a payload, which the wire protocol didn't actually support end to end yet (phase D's plan
explicitly stubbed this: `frame_write(fd, RES_OK, NULL, 0)` with a comment that payload support
"can be added later if a scenario needs it" — and now one does). Completed as part of this phase:
`StageEvent` gained a `payload`/`payload_len` field, `scenario.c` parses an optional `"payload"`
string per scripted event, and `handlers.c`'s `OUTCOME_OK` case sends it. `TcpDeviceConnection`
needed no change — it already read whatever payload came back.

## Cross-transport demo mode — as built

`demo.py` gets a `--tcp [PORT]` flag (`argparse`, `nargs="?"`: bare `--tcp` auto-picks a fresh free
port per scenario; `--tcp 9500` pins one). Every scenario function's `_run()` call now takes both
the mock ingredients (`state`, `behavior`) *and* the matching scenario filename; `_run` picks one
of two paths based on a module-level flag set once in `main()`:

```python
def _run(state, behavior, request, scenario_file) -> None:
    if _tcp_port is not None:
        with _launch_simulator(scenario_file) as target:
            _run_with_provider(TcpConnectionProvider(), target, request)
    else:
        _run_with_provider(MockConnectionProvider(state, behavior), TARGET, request)
```

Went with one subprocess launch per `_run()` call (not per top-level scenario *function* — several,
like `scenario_extraction_modes`, make multiple `_run()` calls each needing a different scenario
file), exactly the simplification this doc originally suggested: the scenario file is fixed at
startup via `argv`, and each `_run()` call is already an independent, freshly-constructed run on
the mock side too. `_launch_simulator` is a context manager: launches the subprocess, polls a
readiness probe (connect-then-close, tolerant of the OS accept backlog) until the port is listening
or the process exits early, yields a `ConnectionTarget`, and terminates the subprocess on exit.

## What "the seam held" means concretely — verified

Running `demo.py` and `demo.py --tcp` back to back and diffing their `RESULT:`/`attempt:`/extraction
lines (timestamps and the `connecting to host:port` line stripped, since ports differ per run)
showed **zero** divergence on the first attempt at the fields the spec actually promises
(`succeeded`, `winning_attack`, `final_phase`, per-attempt `status`/`failed_stage`/`restarts_used`,
and the extraction summary's `succeeded`/`partial`/`files` counts) across all 9 scenarios.

Two *free-text diagnostic* differences did show up on a first pass (a file-not-found reason
omitting the path on the C side; a mid-read connection-drop message with less detail on the Python
side) — worth fixing for genuine parity even though the plan's own comparable-fields list doesn't
require exact reason-string equality. Both were one-line fixes (`handlers.c`'s `handle_read_file`
now builds the reason with `snprintf`; `TcpDeviceConnection.read_file` catches and re-raises with
the path). After that, all 67 comparable lines across every scenario matched exactly.

## README updates — done

- The wire protocol table from the overview (condensed to essentials), including the `RUN_STAGE`
  scripted-payload note.
- Build: `cd Simulator && make`. Run standalone: `./simulator <port> scenarios/<file>.json`.
- Point Python at it: `TcpConnectionProvider()` in place of `MockConnectionProvider` — the entire
  swap, per phase E.
- A "Notable design tradeoffs" section covering config-driven simulator, the generated shared
  protocol module, crash-then-close semantics, single-threaded accept loop, and fixed-size
  length-checked buffers — each with the one-line "why."

## Tests

Phase F itself doesn't add new pytest assertions beyond what phase E already covers — its
"test" is the diff described above, run manually or as a small script that runs both transports
and asserts equality on the comparable fields. This is deliberately a looser, narrative-level check
rather than another layer of unit tests: phases B through E already have granular coverage: this
phase is about confirming the *whole* thing composes correctly end to end.
