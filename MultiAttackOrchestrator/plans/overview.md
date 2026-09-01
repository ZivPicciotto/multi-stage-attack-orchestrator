# Part 1 — Multi-Stage Attack Orchestrator: Overview

## Purpose & scope

Part 1 is the **Python framework**. It models attacks made of multiple stages, decides which
attack to run against a given device, runs the chosen attack, and extracts data once an attack
succeeds.

**Hard boundary for Part 1:** it does *not* talk to a real device. Everything the framework
needs from "the device" goes through one seam — the `DeviceConnection` protocol. In Part 1 that
seam is backed by an **in-memory fake**. In Part 2 the same seam is backed by a **TCP client**
talking to the C simulator. Nothing above the seam changes between the two. Getting this seam
right is the single most important design goal of Part 1, because it is what makes Part 2 a
drop-in rather than a rewrite.

## Design philosophy

The whole framework is a **pipeline of dumb steps driven by smart coordinators**:

- **Stages are data + a single attempt.** A stage knows its own metadata (estimated success
  rate, retry budget, whether failing crashes the device) and how to *attempt itself once*. It
  does **not** decide what to do about its own failure.
- **Orchestrators own all control flow.** "Retry or abort?", "try the next attack?", "reconnect
  and restart?", "now extract data" — every decision lives in an orchestrator, never in a stage.
- **The device is the authority on outcomes.** A stage doesn't roll dice locally; it asks the
  connection to run the step, and the connection (fake or real) reports success/failure or drops.
  See "Where does probability live?" below — this is the key conceptual decision.

### Where does probability live? (the central design decision)

Each stage carries a `success_probability`, but that number is **the attacker's *estimate*,
used only to rank attacks** — it is *not* what determines whether a stage actually works. The
**device decides reality**: the connection's `run_stage()` returns the true verdict (and may drop
the connection entirely).

This mirrors the real world: a forensic tool picks the exploit chain it *believes* is most
reliable (from historical success rates), but whether the exploit actually lands is determined by
the device in front of it. It also cleanly divides the two halves of the exercise:

- **Part 1 (selection)** uses the client-side estimated probabilities to *choose and rank*.
- **Part 2 (behavior)** uses the device/simulator to *determine actual outcomes*, including the
  "connection drops partway through a chain" case the exercise calls out.

In Part 1 the in-memory fake plays the device's role and is **scriptable**, so tests can force
exact outcomes ("stage 2 fails once then succeeds", "connection drops on stage 3") deterministically.

### Ranking metric

An attack's overall score = **the product of its stages' success probabilities** (treating stages
as independent events). The resolver ranks compatible attacks by this value, descending.

This is a deliberate simplification. Real tools weigh at least two more axes we don't model:
**yield** (how much of the filesystem an attack actually reaches — keychain-only vs. full image)
and **wipe-risk** (a failed passcode attempt can increment iOS's attempt counter and *destroy the
evidence* — a far harder constraint than "costs time to retry"). The per-attack `max_restarts`
budget is a partial nod to the latter: a cheap, unpatchable bootrom attack tolerates many restarts;
a passcode attack that risks a wipe sets it to 0. We note these tradeoffs here and in the README
rather than build a multi-axis scorer.

### Kinds of failure, handled differently

| Kind | How it appears | Orchestrator reaction |
|------|----------------|-----------------------|
| **Clean stage failure** (exploit missed, device intact) | `run_stage()` returns `succeeded=False, crashed=False` | Retry in place up to `max_retries`, then give up on this attack. |
| **Crashing stage failure** (exploit failed *and* panicked the device) | `run_stage()` returns `succeeded=False, crashed=True` | Reconnect + restart the whole chain (bounded by `max_restarts`); if exhausted, give up. |
| **Connection fault** (device stopped responding / timed out) | `ConnectionLostError` raised (incl. `ConnectionTimeout`) | Same as a crash: reconnect + restart the whole chain (bounded by `max_restarts`); if exhausted, give up. |

Whether a failure crashed the device is **the device's verdict, not a fixed property of the
stage** — the same exploit can miss cleanly on one attempt and panic the device on the next. A
clean failure is a valid protocol response ("nope, retry"); a crash and a dropped/timed-out socket
both mean device state can no longer be trusted, so the chain restarts. Keeping the clean-vs-crash
verdict on the return value and transport loss on the exception channel is deliberate.

## Component map (data flow)

```
OrchestratorConfig (target + extraction request)
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ MultiAttackOrchestrator            phase: CONNECTING → … → DONE/FAILED │
│                                                                       │
│  1. DeviceSession.open()  ──uses──▶ DeviceConnectionProvider.connect() │
│                                        └─▶ InMemoryDeviceConnection    │
│                                            (Part 2: TcpDeviceConnection)│
│  2. DeviceInfoProvider.get_info(conn) ───▶ DeviceInfo                  │
│  3. AttackResolver.resolve(info, catalog) ─▶ [Attack] ranked           │
│  4. for each candidate Attack:                                         │
│       re-check DeviceInfo (state may have changed) ─▶ skip if unfit    │
│       SingleAttackOrchestrator.run(attack, session)                   │
│              │  runs stages, retry/abort/restart per policy            │
│              ▼                                                          │
│         AttackResult (success | failure@stage | skipped)              │
│       if success: DataExtractor.extract(request, conn) ─▶ ExtractionOutcome │
│  5. assemble MultiAttackResult                                         │
│  6. DeviceSession.close()   (owns the connection lifecycle)           │
└─────────────────────────────────────────────────────────────────────┘
```

Everything below the `DeviceConnection` line is the **only** code that changes for Part 2.

## File structure

```
MultiAttackOrchestrator/
├── plans/                          # these planning docs
│   ├── overview.md                 # ← you are here
│   ├── phase1-foundations.md
│   ├── phase2-connection.md
│   ├── phase3-resolution.md
│   ├── phase4-execution.md
│   ├── phase5-extraction.md
│   └── phase6-orchestration.md
├── pyproject.toml                  # package metadata + dev deps (pytest, mypy)
├── orchestrator/                   # the importable package
│   ├── __init__.py
│   ├── models/                     # pure types, no I/O
│   │   ├── __init__.py
│   │   ├── device.py               # DeviceInfo, IOSVersion, DeviceCompatibilityReqs
│   │   ├── attack.py               # SingleStage, Attack
│   │   ├── results.py              # StageResult, AttackResult, FileResult,
│   │   │                           #   ExtractionOutcome, MultiAttackResult
│   │   ├── context.py              # SingleAttackSharedContext (shared pipeline context)
│   │   ├── phases.py               # OrchestrationPhase
│   │   └── extraction.py           # ExtractionMode, ExtractionRequest
│   ├── connection/
│   │   ├── __init__.py
│   │   ├── base.py                 # DeviceConnection (Protocol) + error hierarchy
│   │   ├── fake.py                 # InMemoryDeviceConnection (Part 1 stand-in)
│   │   ├── provider.py             # DeviceConnectionProvider
│   │   └── session.py              # DeviceSession (owns/refreshes the connection)
│   ├── device_info.py              # DeviceInfoProvider
│   ├── resolver.py                 # AttackResolver
│   ├── attacks/
│   │   ├── __init__.py
│   │   └── catalog.py              # sample Attack + stage definitions
│   ├── execution.py                # SingleAttackOrchestrator
│   ├── extraction.py               # DataExtractor
│   ├── config.py                   # ConnectionTarget, ExtractionRequest, OrchestratorConfig
│   ├── orchestrator.py             # MultiAttackOrchestrator
│   └── demo.py                     # small runnable example against the fake (optional)
└── tests/
    ├── __init__.py
    ├── conftest.py                 # shared fixtures (scripted fakes, sample devices)
    ├── test_models.py              # phase 1
    ├── test_connection.py          # phase 2 (fake + session + reconnect)
    ├── test_resolver.py            # phase 3
    ├── test_execution.py           # phase 4 (retry / crash-restart / drop)
    ├── test_extraction.py          # phase 5 (each mode + partial)
    └── test_orchestrator.py        # phase 6 (end-to-end against the fake)
```

`orchestrator/` (snake_case, idiomatic Python import root) nests inside the PascalCase project
folder `MultiAttackOrchestrator/`. Imports read `from orchestrator.resolver import AttackResolver`.

## Phase roadmap

| Phase | Doc | Deliverable | Depends on |
|-------|-----|-------------|------------|
| 1 | `phase1-foundations.md` | Pure types: enums, results, `DeviceInfo`, reqs, `SingleStage`/`Attack` shapes, `SingleAttackSharedContext` | — |
| 2 | `phase2-connection.md` | `DeviceConnection` protocol + error types, in-memory fake, provider, `DeviceSession` | 1 |
| 3 | `phase3-resolution.md` | Sample attack catalog, `AttackResolver` (filter + rank) | 1, 2 |
| 4 | `phase4-execution.md` | `SingleAttackOrchestrator` (retry / crash-restart / drop) | 1, 2, 3 |
| 5 | `phase5-extraction.md` | `DataExtractor` + the four extraction modes | 1, 2 |
| 6 | `phase6-orchestration.md` | `MultiAttackOrchestrator`, config, `MultiAttackResult`, demo | 1–5 |

Each phase ships with its own unit tests against the scriptable fake. Integration tests that run
against the **real C simulator** are Part 3 — but because everything routes through
`DeviceConnection`, those tests reuse the exact same orchestration code, only swapping the
provider.

## Testing strategy (Part 1)

- **Deterministic by construction.** The fake is scripted (or seeded), so no test depends on
  luck. Probability drives *ranking* (pure arithmetic, trivially testable) and, when we want it,
  the fake's own seeded outcomes — never an unseeded coin flip inside a test.
- **Each seam tested in isolation** (reqs matching, resolver ordering, extractor modes) plus
  **end-to-end** flows through `MultiAttackOrchestrator` against the fake.
- **The awkward cases are the point:** connection drops mid-chain, a crash-on-failure stage
  forcing a full restart, an attack becoming incompatible after battery drain, partial
  multi-file extraction. These are called out per-phase.

## Python notes (recurring themes)

- **Value types** (`DeviceInfo`, results, config) use `@dataclass(frozen=True)` — Python objects
  are reference types by default; `frozen=True` is how you get immutable, value-like semantics.
- **Interfaces:** `DeviceConnection` is a `typing.Protocol` (structural — the TCP client conforms
  without inheriting, which suits a cross-process boundary). `SingleStage` is a concrete base
  class with a default `attempt()`, subclassed only for special stages.
- **Result types are dataclasses, not rich enums:** `StageResult`/`AttackResult` carry a status
  field plus payload fields, since Python enums can't attach per-case data.
- **`None` is not compiler-checked.** Boundaries (`ExtractionRequest`, parsing device info)
  validate at runtime in `__post_init__`; internal code trusts its types.
