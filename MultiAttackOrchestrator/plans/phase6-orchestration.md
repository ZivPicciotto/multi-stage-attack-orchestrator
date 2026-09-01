# Phase 6 — Multi-attack orchestration & results

**Goal:** tie everything together. Connect, learn the device, pick attacks, try them in order
(re-checking state each time), extract on the first success, and report the whole run as one
`MultiAttackResult`. This is the top-level entry point and the object a caller actually uses.

**Depends on:** phases 1–5. **Unlocks:** the demo, and Part 3's integration tests (which reuse
this unchanged, swapping only the provider).

**Files:** `orchestrator/config.py`, `orchestrator/orchestrator.py`, `orchestrator/demo.py`

---

## `config.py` — the request

```python
@dataclass(frozen=True)
class ConnectionTarget:
    host: str
    port: int

@dataclass(frozen=True)
class OrchestratorConfig:
    target: ConnectionTarget
    request: ExtractionRequest        # what to do once in (unlock / single / multi / all)
```

**Decision — split "where" from "what," bundle at the top.** `ConnectionTarget` (reaching the
device) and `ExtractionRequest` (intent once in) are different concerns; bundling them in
`OrchestratorConfig` gives `run()` a single argument without conflating the two. **Decision — top-
level dataclasses, not nested in the orchestrator class.** Python namespaces at the *module* level;
a `Configuration` nested inside the orchestrator class just complicates imports and type hints here.

## `orchestrator.py` — MultiAttackOrchestrator

```python
class MultiAttackOrchestrator:
    def __init__(self, provider, resolver=AttackResolver(),
                 info_provider=DeviceInfoProvider(),
                 single=SingleAttackOrchestrator(),
                 extractor=DataExtractor()): ...

    def run(self, config: OrchestratorConfig) -> MultiAttackResult:
```

Dependencies are injected (provider, resolver, sub-orchestrator, extractor), so tests supply a
scripted `FakeConnectionProvider` and the real logic runs unchanged. In Part 2, pass a
`TcpConnectionProvider` — nothing else moves.

### The flow (phases annotated)

```python
def run(self, config) -> MultiAttackResult:
    attempts: list[AttackResult] = []
    phase = OrchestrationPhase.CONNECTING
    with DeviceSession(self.provider, config.target) as session:          # CONNECTING; owns lifecycle
        try:
            phase = OrchestrationPhase.GATHERING_INFO
            info = self.info_provider.get_info(session.connection)        # reads LIVE (mutable) state
        except ConnectionLostError as e:
            return MultiAttackResult.failure(config.request.mode,
                       final_phase=GATHERING_INFO, error=str(e), attempts=())

        candidates = self.resolver.resolve(info)                          # RESOLVING_ATTACKS
        if not candidates:
            return MultiAttackResult.failure(config.request.mode,
                       final_phase=RESOLVING_ATTACKS,
                       error="no compatible attack for this device", attempts=())

        try:
            for attack in candidates:
                info = self._recheck(session)                             # re-read CURRENT state per attempt
                if info is None:                                          # couldn't even re-read
                    attempts.append(AttackResult.skipped(attack.id, "device unreachable"))
                    continue
                if not attack.requirements.matches(info):                 # state drifted (e.g. battery drained)
                    attempts.append(AttackResult.skipped(attack.id,
                                        "; ".join(attack.requirements.reasons_incompatible(info))))
                    continue

                phase = OrchestrationPhase.RUNNING_ATTACK
                result = self.single.run(attack, session)
                attempts.append(result)
                if result.succeeded:
                    phase = OrchestrationPhase.EXTRACTING_DATA
                    extraction = self.extractor.extract(config.request, session.connection)
                    return MultiAttackResult.success(
                        config.request.mode, winning_attack=attack.id,
                        attempts=tuple(attempts), extraction=extraction,
                        final_phase=DONE)
        except ProtocolError as e:                                        # desync — no retry fixes it
            return MultiAttackResult.failure(config.request.mode,
                       final_phase=phase, error=f"protocol desync: {e}",
                       attempts=tuple(attempts))

        return MultiAttackResult.failure(config.request.mode,             # all tried, none worked
                   final_phase=RUNNING_ATTACK,
                   error="all viable attacks failed", attempts=tuple(attempts))
```

`_recheck(session)` re-reads device info **from the current (mutable) device state**, reconnecting
once via the session if the read hits a `ConnectionLostError`; returns `None` if it still can't
reach the device. Because the fake mutates its `DeviceState` (phase 2), a drained battery or a
rebooted device genuinely shows up here — that's what makes the state-drift skip real rather than
scripted.

### Decisions

- **Re-check state before every attempt.** Battery drains, devices reboot; an attack viable at the
  start may be unfit by attempt #3. We re-read info and re-run `matches`, recording a `SKIPPED`
  result (with reasons) rather than blindly running. Cheap, and more honest than trusting the
  opening snapshot.
- **First success wins and short-circuits.** The moment an attack succeeds we extract and return —
  no reason to try lower-ranked attacks. Extraction runs on **that attack's live connection**,
  because the unlocked state lives on that specific session (reconnecting would lose it).
- **The session owns the connection; `run` owns the session.** `with DeviceSession(...)` guarantees
  the connection is closed on every exit path — success, all-fail, or exception. This is the
  concrete form of "the top orchestrator owns the connection lifecycle."
- **Every path returns a `MultiAttackResult`.** No `DeviceError` escapes `run`. Connection loss is
  handled where it occurs (restart in the sub-orchestrator, reconnect in `_recheck`, a
  `GATHERING_INFO` failure at the top). `ProtocolError` — the two sides desynced on the wire — is a
  bug no retry fixes, so it's caught once at the top level and turned into a `FAILED` result at
  whatever `phase` we'd reached, rather than being allowed to crash the run. The result's
  `final_phase` says how far we got, `attempts` records every attack tried and where each failed,
  and `extraction` carries the (possibly partial) data. That's the complete, inspectable report the
  prompt asks for.

## `demo.py` (optional but recommended)

A tiny `__main__` that builds a probabilistic `FakeConnectionProvider`, a sample
`OrchestratorConfig` (e.g. `all_files`), runs the orchestrator, and pretty-prints the
`MultiAttackResult`. Gives a reviewer a one-command way to see the whole thing move without
reading tests, and becomes the template for the Part 2 run against the real simulator.

## Tests (`test_orchestrator.py`) — end-to-end through the fake

- **No compatible attack:** device fits nothing → failure, `final_phase=RESOLVING_ATTACKS`, empty
  attempts.
- **First fails, second wins:** provider scripts attack #1 to fail and #2 to succeed → result
  `succeeded`, `winning_attack` = #2, `attempts` shows #1 failed at its stage.
- **All fail:** every candidate fails → failure, `final_phase=RUNNING_ATTACK`, attempts lists each.
- **State drift skip:** battery re-reads below an attack's `min_battery` on re-check → that attack
  `SKIPPED` with a reason, a lower-requirement attack still tried.
- **Each extraction mode end-to-end:** on a win, `unlock` / `single` / `multi` / `all` produce the
  right `ExtractionOutcome`.
- **Lifecycle:** the session's connection is closed on success, on all-fail, and when an exception
  is raised mid-run (assert `close` called on every path).
- **Connection drop during info-gathering:** first `get_info` raises → failure with
  `final_phase=GATHERING_INFO`.
- **Protocol desync is fatal, not retried:** a stage scripted to raise `ProtocolError` → failure
  with `final_phase=RUNNING_ATTACK` and a "protocol desync" error; no restart attempted.
