# Multi-Stage Attack Orchestrator

A framework that models multi-stage mobile-device unlocking attacks, decides which attack to run
against a given device, runs it, and extracts data once it succeeds. Built for a take-home
exercise; see `Attack_Orchestrator_Exercise.docx` for the original brief.

This is a **software design exercise**: nothing here talks to a real device or performs a real
exploit. "Attacks," "stages," and "crashes" are simulation vocabulary.

## Status

| Part | What | Status |
|---|---|---|
| **1 — Attack framework** | Python: model attacks, pick one, run it, extract data | **Done.** 84 tests passing, mypy clean. |
| **2 — Device simulator** | C TCP server standing in for a real device | **Designed, not yet built.** Wire protocol and phase-by-phase plan are written; no C code exists yet. |
| **3 — Tests against the simulator** | Integration tests that exercise Part 1 over a real socket, not just the in-memory mock | Not started — depends on Part 2. |

## Repo layout

```
Attack_Orchestrator_Exercise.docx   # the original brief
MultiAttackOrchestrator/            # Part 1 — Python framework (done)
├── plans/                          # design docs, written before the code, phase by phase
├── orchestrator/                   # the importable package
└── tests/                          # pytest suite
Simulator/                          # Part 2 — C TCP simulator (planned)
└── plans/                          # wire protocol + phase-by-phase plan; no code yet
```

Each part's `plans/` folder holds the reasoning this README only summarizes — start with
`overview.md` in either one for the full picture.

## Part 1 — the attack framework

### The one seam

Every part of the framework that needs "the device" goes through a single abstraction,
`DeviceConnection` (`orchestrator/connection/base.py`) — a `typing.Protocol` with five methods:
`get_device_info`, `run_stage`, `list_files`, `read_file`, `close`. Part 1 backs it with an
in-memory `InMemoryDeviceConnection` (`orchestrator/connection/mock.py`); Part 2's job is to add a
`TcpDeviceConnection` that satisfies the exact same contract. Nothing above that line —
resolution, execution, extraction, the top-level orchestrator — needs to change when the transport
does. That was the single most important design constraint while building Part 1.

### Where does probability live?

Each stage carries a `success_probability`, but that's the attacker's **estimate**, used only to
*rank* candidate attacks. It is not what determines whether a stage actually works — **the device
decides reality**. `run_stage()` on the connection returns the true verdict (or the connection
drops). This mirrors how a real forensic tool operates: it picks the exploit chain it *believes*
is most reliable from historical data, but the device in front of it determines what actually
happens. It also cleanly splits the exercise: Part 1 uses estimated probabilities to *choose*;
Part 2's simulator is the thing that *decides outcomes*.

### Ranking metric

An attack's score is the product of its stages' success probabilities (independent-events
assumption), and the resolver (`orchestrator/resolver.py`) ranks compatible attacks by that value,
descending.

**This is a deliberate simplification, and there was more than one reasonable option here.** Real
tools weigh at least two more axes: **yield** (keychain-only vs. a full filesystem image) and
**wipe-risk** (a failed passcode attempt can burn iOS's limited attempt counter and destroy the
evidence — categorically worse than "costs time to retry"). Building a real multi-axis scorer felt
like scope creep for what the exercise is testing, so instead the catalog encodes cost-of-failure
through a narrower knob: `Attack.max_restarts`. A cheap, unpatchable bootrom exploit tolerates
several full-chain restarts; a brute-force passcode attack sets `max_restarts=0`, so a bad attempt
is never retried at the cost of the evidence. It's a partial answer, and the README says so rather
than pretending the product-of-probabilities metric is the whole story.

### Three kinds of failure, three different reactions

| Kind | How it appears | Orchestrator reaction |
|---|---|---|
| Clean stage failure (exploit missed, device intact) | `StageResult` with `succeeded=False, crashed=False` | Retry in place up to the stage's `max_retries`, then give up on this attack. |
| Crashing stage failure (exploit failed *and* panicked the device) | `StageResult` with `crashed=True` | Reconnect and restart the whole chain, bounded by the attack's `max_restarts`. |
| Connection fault (transport died mid-call) | `ConnectionLostError` raised (or its `ConnectionTimeout` subtype) | Same as a crash: reconnect and restart the whole chain. |

The first two are **return values**; the third is an **exception**. That split is deliberate, not
incidental: a clean failure or a crash is a completed round-trip carrying the device's verdict — a
normal outcome the protocol is designed to express. A dropped or timed-out connection means no
verdict arrived at all. Conflating the two would blur "the device told me it broke" with "I have no
idea what happened" — two situations with the same *recovery* (restart the chain) but different
diagnostic value. See `orchestrator/connection/mock.py` for exactly how a mocked crash reaches this
distinction: `run_stage()` returns a normal `StageResult(crashed=True)`, and only the connection's
*next* call raises `ConnectionLostError`, because the device's last gasp before going unresponsive
is itself a valid response. Part 2's wire protocol (below) mirrors this precisely on purpose.

### The shared context

`SingleAttackSharedContext` (`orchestrator/models/context.py`) is threaded through every stage in
a chain attempt — a scratchpad stages can write to and later stages can read from, reset fresh on
every restart (since a crash resets the device too, any accumulated values are stale). Most stages
never touch it; `ContextDependentStage` (`orchestrator/models/attack.py`) is the one that does — a
stage whose `attempt()` refuses to even contact the device if a dependency isn't in the context yet
(`orchestrator/attacks/catalog.py`'s `KEYBAG_CHAIN`: a class-key leak feeds a keybag-unwrap stage
that cannot run without it). That refusal is a plain `StageResult.fail(...)`, not a new control-flow
path — the orchestrator's existing retry/restart/fallback logic handles it unchanged.

### Extraction

Once an attack succeeds, `DataExtractor` (`orchestrator/extraction.py`) pulls data off the device
via `list_files`/`read_file` on the same connection, in four modes (`unlock`-only, `single_file`,
`multi_files`, `all_files`). Extraction is per-file: `ExtractionOutcome` carries one `FileResult`
per requested path, so a run can be reported as **partial** (some files pulled, some missing or the
connection dropped mid-pull) rather than collapsing to a single pass/fail boolean.

### Illegal states, made unrepresentable

Two of the value types went through a deliberate typing pass: `StageResult` originally stored
`succeeded`/`crashed` as two independent booleans, which could represent the nonsensical
"succeeded and crashed" — it's now a `StageResultType` enum (`SUCCESS`/`LOGIC_FAILURE`/`CRASH`)
with `succeeded`/`crashed` as derived properties. `FileResult` originally stored `succeeded` as its
own field alongside `data`, which could drift out of sync with the data it described — `succeeded`
is now inferred from `data is not None` (checked with `is not None`, not truthiness, so a
legitimately empty file isn't misreported as a failure). Same idea applied to `AttackResult`, which
uses an `AttackResultType` enum for the same reason.

### Running it

```bash
cd MultiAttackOrchestrator
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # requires Python >= 3.11

.venv/bin/pytest -q                                           # 84 tests
.venv/bin/mypy orchestrator --ignore-missing-imports           # clean

.venv/bin/python -m orchestrator.demo                          # narrated end-to-end scenarios
```

`demo.py` runs eight scenarios end-to-end against the mock — happy path, retry-then-succeed,
crash-then-restart, connection-drop-then-restart, fallback-after-failure, battery-drift causing a
mid-run skip, total failure, all four extraction modes, and the context-dependency stage from
above — with full INFO-level logging so the narrative (stage attempts, retries, restarts, skips) is
visible, not just the final result.

## Part 2 — the device simulator (planned)

Not yet implemented — this section describes the design in `Simulator/plans/`, so the intent is on
record even though the C code doesn't exist yet.

### Design goal

A single-threaded C TCP server that plays the device's role for real, well enough that
`TcpDeviceConnection` satisfies `DeviceConnection` exactly like the mock does — meaning zero changes
to anything above the seam (`MultiAttackOrchestrator`, `SingleAttackOrchestrator`, `AttackResolver`,
`DataExtractor`). Only two new Python files get added: `connection/tcp.py`
(`TcpDeviceConnection` + `TcpConnectionProvider`) and a generated `wire_protocol.py`.

### Config-driven, not compiled-in

The simulator has no `switch` over specific stage IDs or file paths. It loads a **scenario JSON
file** at startup (device attributes, a virtual filesystem, per-stage scripted outcomes) and
interprets it generically — deliberately mirroring Part 1's `DeviceState` + `ScriptedBehavior`
shape, so a test author learns one mental model for both sides. Stage IDs and paths are pure data
on the wire, never something that needs to stay in sync in *compiled* code on either side.

**What's genuinely shared vs. not:** a literal shared module across Python and C isn't possible,
but a single source of truth for the parts that must match bit-for-bit is — the wire opcodes and
frame format are generated from one `protocol/spec.json` into both a Python module and a C header,
with a codegen-diff test guarding against drift. Stage IDs/paths don't need that treatment, since
the simulator never hardcodes them.

### Wire protocol v1

Frame, both directions: `[1 byte type][4 bytes length, big-endian][length bytes payload]`.

| Byte | Request | Payload |
|---|---|---|
| `0x01` | `REQ_GET_INFO` | *(empty)* |
| `0x02` | `REQ_RUN_STAGE` | stage_id (UTF-8) |
| `0x03` | `REQ_LIST_FILES` | *(empty)* |
| `0x04` | `REQ_READ_FILE` | path (UTF-8) |

| Byte | Response | Meaning | After sending |
|---|---|---|---|
| `0x81` | `RES_OK` | success (payload varies per request) | connection stays open |
| `0x82` | `RES_FAIL` | clean logical failure (`RUN_STAGE` only) | connection stays open |
| `0x83` | `RES_CRASH` | failure that also crashed the device (`RUN_STAGE` only) | **server closes the socket** |
| `0x84` | `RES_FILE_ERROR` | file missing/inaccessible (`READ_FILE` only) | connection stays open |
| `0x85` | `RES_PROTOCOL_ERROR` | malformed/unrecognized request | server closes the socket |

No explicit close request — the client just closes the TCP connection; the server detects EOF and
returns to `accept()`.

**The crash design point, matching Part 1 exactly:** `RES_CRASH` sends a *complete* frame — the
client still learns the reason — and only *then* does the server close the socket. A `DROP`
scenario is the opposite: zero bytes written, socket closed immediately, indistinguishable from a
real network failure. This is precisely the return-value-vs-exception split Part 1 already commits
to (see above), so `SingleAttackOrchestrator`'s logic runs unchanged against a real simulator.
Timeouts are purely client-side (`socket.settimeout()` → `ConnectionTimeout`); no server support
needed for v1.

### Planned build/run (once implemented)

```bash
cd Simulator && make
./simulator <port> scenarios/03_crash_then_restart.json
```

```python
from orchestrator.connection.tcp import TcpConnectionProvider
provider = TcpConnectionProvider()   # drop-in for MockConnectionProvider
```

### Why cJSON, vendored

The scenario format needed real JSON parsing, and hand-rolling one for a take-home exercise felt
like effort spent on the wrong thing. cJSON (MIT-licensed, single C file) is vendored directly into
`Simulator/third_party/` rather than pulled as a system dependency, so `make` has no external
requirements beyond a C compiler.

## Part 3 — tests against the simulator

Not started; depends on Part 2 existing. The plan (`Simulator/plans/overview.md`, phase F) is to
reuse the exact scenario JSON files and `TcpConnectionProvider` from Part 2, and run the *same*
behavioral test list Part 1's `test_connection.py` already runs against the mock (scripted retry,
crash kills the connection, reconnect revives it, battery drain, drop-on-read) against a real
simulator subprocess instead. If those pass unchanged in shape, the seam held end to end.

## Notable design tradeoffs (more than one reasonable option existed)

- **Product-of-probabilities ranking vs. a multi-axis scorer** — see "Ranking metric" above; chose
  the simpler metric and documented what it leaves out (yield, wipe-risk) rather than build a
  scorer the exercise wasn't really asking for.
- **Exceptions for transport faults, return values for logical outcomes** — rejected using
  exceptions for everything (would make ordinary retry-driving control flow via `except`, which
  reads worse and conflates "the device answered" with "nothing came back").
- **A `typing.Protocol` for `DeviceConnection`, not an ABC** — structural typing means
  `TcpDeviceConnection` in Part 2 satisfies the contract without inheriting from anything in Part
  1, which suits a class that lives across a process/language boundary.
- **Config-driven C simulator over compiled-in stage IDs** — considered hardcoding the sample
  catalog's stage IDs directly in C, but that would mean every new Python-side attack needs a
  matching C code change, defeating the point of a generic device stand-in.
- **Generated protocol module/header from one spec vs. two hand-maintained copies** — the wire
  format is the one place Python and C genuinely must agree bit-for-bit, so it's generated from a
  single `protocol/spec.json` with a drift-detecting test, rather than trusted to stay in sync by
  hand.
