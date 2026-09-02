# Part 2 — Device Simulator: Overview

## Purpose & scope

Part 2 is a **C TCP server** that plays the role of "the device" for real, over a socket, so the
Python framework built in Part 1 can talk to something other than its in-memory mock. The whole
point of Part 1's design was that everything routes through one seam — `DeviceConnection` — so
that swapping the mock for a real transport requires **zero changes** to `MultiAttackOrchestrator`,
`SingleAttackOrchestrator`, `AttackResolver`, or `DataExtractor`. Part 2 is where that promise gets
tested. Only one new Python class (`TcpDeviceConnection`) and one new provider
(`TcpConnectionProvider`) get added; nothing else in `orchestrator/` changes.

## Design philosophy

The simulator is **config-driven, not compiled-in**. It has no `switch` statement over specific
stage IDs or file paths — it loads a scenario file at startup describing device attributes, a
virtual filesystem, and scripted per-stage/per-path outcomes, and interprets that data generically.
This mirrors Part 1's `DeviceState` + `ScriptedBehavior` almost exactly, on purpose: the same
scenario shape that scripts the Python mock also scripts the C simulator, so a test author
learns one mental model for both. Stage IDs and file paths are therefore pure data flowing through
the wire protocol — never something that needs to be kept in sync in *compiled* code on either
side.

## What is genuinely shared vs. what is not

From the earlier discussion on this: a literal shared module across Python and C isn't possible,
but a single source of truth is. The line we're drawing:

- **The wire-level opcodes and frame format** genuinely must match bit-for-bit on both sides —
  these come from one generated source (`SharedProtocol/spec.json` → a Python module and a C header).
- **Stage IDs and file paths do not** need to be compiled constants anywhere, because the
  simulator is config-driven. They're just strings in scenario JSON and in the Python attack
  catalog. `spec.json` still lists a canonical vocabulary purely as shared documentation (so
  scenario authors don't invent near-duplicate names), but nothing breaks if it drifts — it's a
  convenience, not a contract.

## Wire protocol v1

**Frame (both directions):** `[1 byte type][4 bytes length, big-endian uint32][length bytes payload]`

**Requests (client → server):**

| Byte | Name | Payload |
|---|---|---|
| `0x01` | `REQ_GET_INFO` | *(empty)* |
| `0x02` | `REQ_RUN_STAGE` | stage_id (UTF-8 text) |
| `0x03` | `REQ_LIST_FILES` | *(empty)* |
| `0x04` | `REQ_READ_FILE` | path (UTF-8 text) |

No explicit close request — the client closes the TCP connection when done; the server detects EOF
on its next read and returns to `accept()`.

**Responses (server → client):**

| Byte | Name | Meaning | Payload | After sending |
|---|---|---|---|---|
| `0x81` | `RES_OK` | success | varies per request (below) | connection stays open |
| `0x82` | `RES_FAIL` | clean logical failure (`RUN_STAGE` only) | reason text | connection stays open |
| `0x83` | `RES_CRASH` | failure that crashed the device (`RUN_STAGE` only) | reason text | **server closes the socket** |
| `0x84` | `RES_FILE_ERROR` | file missing/inaccessible (`READ_FILE` only) | reason text | connection stays open |
| `0x85` | `RES_PROTOCOL_ERROR` | malformed/unrecognized request | reason text | server closes the socket |

`RES_OK` payload per request:
- `GET_INFO` → `"<model>|<major>.<minor>.<patch>|<battery>"`, e.g. `"iPhone11,8|14.2.0|60"`
- `RUN_STAGE` → optional opaque bytes (→ `SingleAttackSharedContext`); may be empty
- `LIST_FILES` → newline-separated paths (empty payload = no files)
- `READ_FILE` → raw file bytes, opaque

**DROP simulation.** Per scenario config, the server can respond to a specific request by closing
the socket **immediately, with zero bytes written** — indistinguishable from a real network
failure. `TcpDeviceConnection` sees `recv()` return 0 (or an error) and raises
`ConnectionLostError`, matching the mock's `DROP` sentinel exactly.

**The crash design point.** A crash is delivered as a *complete, normal* `RES_CRASH` frame — the
client still learns the reason — and *then* the server closes the socket. This mirrors the mock
precisely: `run_stage()` returns a `StageResult` with `crashed=True` and a reason; only the
*next* call on that connection fails with `ConnectionLostError`. This is what lets
`SingleAttackOrchestrator`'s logic (phase 4, Part 1) run completely unchanged against the real
simulator.

**Timeouts** are purely client-side (`socket.settimeout()` around each send/recv →
`ConnectionTimeout` on expiry). No server support is needed for v1; a "the server just never
responds" scenario type is a cheap future addition but isn't required for the exercise.

## Scenario configuration (the C-side ScriptedBehavior)

A JSON file, loaded once at simulator startup, parsed with **cJSON** (vendored — see phase C):

```json
{
  "device": { "model": "iPhone11,8", "ios_version": "14.2.0", "battery": 90 },
  "filesystem": { "/private/var/mobile/Library/SMS/sms.db": "<sms data>" },
  "stages": {
    "kernel_rw": [{"outcome": "crash", "reason": "kernel panic"}, {"outcome": "ok"}]
  },
  "battery_drain": { "bootrom": 25 },
  "drop_on_read": ["/private/var/mobile/Media/DCIM/IMG_0001.jpg"]
}
```

Semantics deliberately mirror Part 1's `ScriptedBehavior` and `DeviceState`:
- An unscripted stage defaults to `ok`, repeatable — same default as the mock.
- Per-stage queues are **consumed** (advanced) across the whole process lifetime, not reset per
  connection — this is what makes "crash → reconnect → succeeds" scenarios work: the second
  connection continues the same queue where the first left off, because device state (and the
  scenario's remaining script) persists in the server process, not in any one socket.
- `battery_drain` applies regardless of the stage's outcome (fail or crash), same as the mock.

## What state does *not* need to exist on the C side

Part 1's `DeviceState.alive` gates further calls on a *dead connection object* — but that concept
doesn't translate to a real socket server. Once a connection is dropped (crash or DROP), the
*socket* is gone; there's nothing further to gate, because a new `accept()` on the still-listening
server represents a fresh, always-successful reconnect at the TCP level. So the C `DeviceState`
equivalent only needs to track: model, iOS version, current battery, the filesystem, and the
scenario's remaining scripted queues — no `alive` flag.

## File structure

```
SharedProtocol/                    # sibling to MultiAttackOrchestrator/ and Simulator/
├── spec.json                      # opcodes + frame format + canonical stage-id vocabulary
└── generate.py                    # stdlib-only codegen; a test re-runs it and diffs for drift

Simulator/
├── plans/                         # these planning docs
├── Makefile
├── third_party/
│   ├── cJSON.c                    # vendored (MIT) — see phase C
│   └── cJSON.h
├── shared_protocol/
│   └── protocol_ids.h             # GENERATED from SharedProtocol/spec.json
├── include/
│   ├── frame.h
│   ├── device_state.h
│   └── scenario.h
├── src/
│   ├── main.c                     # arg parsing (port, scenario file), starts the server
│   ├── server.c                   # socket bind/listen, single-threaded accept loop
│   ├── frame.c                    # encode/decode the wire frame
│   ├── device_state.c             # model/iOS/battery/filesystem — mirrors DeviceState
│   ├── scenario.c                 # loads + consumes scripted-behavior JSON via cJSON
│   └── handlers.c                 # the 4 request handlers
└── scenarios/                     # one JSON per demo scenario, mirroring Part 1's demo.py
    ├── 01_happy_path.json
    ├── 02_retry_then_succeed.json
    ├── 03_crash_then_restart.json
    ├── 04_connection_drop_then_restart.json
    ├── 05_fallback_after_failure.json
    ├── 06_state_drift_skip.json
    └── 07_all_attacks_fail.json

MultiAttackOrchestrator/orchestrator/
├── shared_protocol/
│   ├── __init__.py                # hand-written re-export (project convention)
│   └── wire_protocol.py           # GENERATED from SharedProtocol/spec.json
└── connection/
    └── tcp.py                     # TcpDeviceConnection, TcpConnectionProvider
```

## Phase roadmap

| Phase | Doc | Deliverable | Depends on |
|---|---|---|---|
| A | `phaseA-shared-protocol.md` | `SharedProtocol/spec.json` + codegen, generated Python module + C header, drift-guard test — **done** | Part 1 (done) |
| B | `phaseB-server-skeleton.md` | TCP accept loop + frame encode/decode — **done** | A |
| C | `phaseC-scenario-state.md` | `device_state.c`, `scenario.c` (cJSON-backed), mirrors `DeviceState`/`ScriptedBehavior` — **done** | A, B |
| D | `phaseD-handlers.md` | The 4 request handlers; crash-then-close and drop-with-no-response — **done** | B, C |
| E | `phaseE-python-tcp-client.md` | `TcpDeviceConnection` + `TcpConnectionProvider`, satisfying `DeviceConnection` exactly — **done**, 13/13 tests passing against the real simulator | A, D |
| F | `phaseF-scenarios-demo.md` | Scenario JSON files mirroring the Part 1 demo scenarios + a `--tcp` demo mode — **done**, 67/67 comparable log lines identical across both transports | D, E |

**Part 2 is complete.** Every phase above is implemented and verified; see each phase doc for what
was actually built (some details — the wire-protocol payload support in particular — were extended
beyond what was originally sketched, once a real scenario needed them).

Part 3 (formal integration tests running against the real simulator) builds directly on phase F's
scenario files and `TcpConnectionProvider` — but is out of scope for this plan.

## Testing strategy for Part 2 itself

Each phase is verified as it's built, same discipline as Part 1:
- **Phase A**: the codegen test (`generate.py` re-run + diff) catches drift immediately.
- **Phase B**: a raw Python socket script (no `TcpDeviceConnection` yet) sends hand-built frames
  and asserts on bytes back — proves framing before any domain logic exists.
- **Phase C**: unit-testable in isolation if `scenario.c`/`device_state.c` expose pure functions
  (parse scenario → state; apply stage event → next state) — a small C test binary or just
  print-and-inspect, given the scope.
- **Phase D**: manual verification per handler using the phase B raw-socket harness.
- **Phase E**: `TcpDeviceConnection` gets the *exact same* behavioral test list Part 1's
  `test_connection.py` used against the mock (scripted retry, crash kills the connection, reconnect
  revives it, battery drain, drop-on-read) — run against a real simulator subprocess instead of the
  mock. If these pass unchanged in shape, the seam held.
- **Phase F**: run `demo.py --tcp` and diff its narrative against the Part 1 mock-backed run — they
  should tell the same story per scenario, modulo timestamps.

## Notes for the README — done

The root README now covers all of this: the wire protocol table (condensed), how to build the
simulator (`make` in `Simulator/`), how to run it standalone, how to point the Python framework at
it (`TcpConnectionProvider()` in place of `MockConnectionProvider`), and the "seam held" proof
(67/67 identical comparable lines across both transports). Folded into phase F rather than a
standalone phase, same precedent as Part 1.
