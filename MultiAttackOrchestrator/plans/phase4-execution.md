# Phase 4 — Single-attack execution

**Goal:** run one attack's stages in order, and handle every failure mode correctly — in-place
retries, crash-induced full restarts, and connection drops. This is where "stages are dumb,
orchestrators are smart" pays off. It is the most logic-heavy phase and the one the tests hammer
hardest.

**Depends on:** phases 1–3. **Unlocks:** phase 6.

**File:** `orchestrator/execution.py`

---

## Contract

```python
class SingleAttackOrchestrator:
    def run(self, attack: Attack, session: DeviceSession) -> AttackResult: ...
```

It runs on `session.connection`, may call `session.reconnect()` on crash/drop, and returns an
`AttackResult`. It never opens or closes the session — the top orchestrator owns that.

## Two nested loops, one clean split

The control flow separates cleanly into "run the chain once" and "how many times to restart it":

```python
def run(self, attack, session) -> AttackResult:
    restarts = 0
    while True:
        context = StageContext()                 # fresh per chain attempt (a restart = device reset)
        outcome = self._run_chain_once(attack, session, context)
        if outcome.kind is SUCCESS:
            return AttackResult.success(attack.id, restarts_used=restarts)
        if outcome.kind is GIVE_UP:
            return AttackResult.failed(attack.id, failed_stage=outcome.stage,
                                       reason=outcome.reason, restarts_used=restarts)
        # outcome.kind is NEEDS_RESTART (crash-on-failure or connection drop)
        if restarts >= attack.max_restarts:
            return AttackResult.failed(attack.id, failed_stage=outcome.stage,
                                       reason=f"{outcome.reason}; restart budget exhausted",
                                       restarts_used=restarts)
        restarts += 1
        session.reconnect()                      # fresh connection, loop restarts the whole chain
```

```python
def _run_chain_once(self, attack, session, context) -> _ChainOutcome:
    for stage in attack.stages:
        attempt = 0
        while True:
            try:
                result = stage.attempt(session.connection, context)
            except ConnectionLostError as e:
                return _ChainOutcome.needs_restart(stage.name, f"connection lost: {e}")
            if result.succeeded:
                break                            # advance to next stage
            # logical failure:
            if stage.crashes_on_failure:
                return _ChainOutcome.needs_restart(stage.name, "stage crashed device")
            if attempt < stage.max_retries:
                attempt += 1
                continue                         # retry in place, same connection
            return _ChainOutcome.give_up(stage.name, result.reason)   # retries exhausted
    return _ChainOutcome.success()
```

`_ChainOutcome` is a private tagged result (`kind ∈ {SUCCESS, GIVE_UP, NEEDS_RESTART}` + optional
`stage`/`reason`). Splitting "one chain attempt" from "the restart loop" makes both independently
testable and keeps the branching legible — no Python `goto` gymnastics.

## The failure decision table (implemented above)

| Situation | Detected as | Reaction |
|-----------|-------------|----------|
| Stage succeeds | `result.succeeded` | advance to next stage |
| Logical fail, retries left, not crash-y | `not succeeded`, `attempt < max_retries` | retry in place (same connection) |
| Logical fail, retries exhausted, not crash-y | `attempt == max_retries` | give up on this attack |
| Logical fail, `crashes_on_failure=True` | flag on the stage | reconnect + restart whole chain |
| Connection drops mid-stage | `ConnectionLostError` | reconnect + restart whole chain |
| Restart budget hit | `restarts >= attack.max_restarts` | give up on this attack |

**Why crash and drop share the "restart" path.** Both mean *device state can no longer be
trusted* — a panicked/rebooted device and a dead socket are the same problem: you can't continue
from stage N, you must start clean. In-place retry is only valid for a *logical* miss that left the
device intact. This distinction is the whole reason phase 2 keeps logical failure (a return value)
separate from a connection fault (an exception).

**Why context resets on restart.** A restart re-runs stage 1 on a fresh device; any tokens a prior
attempt wrote to the context are stale, so each chain attempt gets a new `StageContext`.

## Tests (`test_execution.py`) — all deterministic via the scripted fake

- **Happy path:** every stage `OK` → `AttackResult.success`, `restarts_used=0`.
- **Retry then succeed:** a non-crash stage scripted `[FAIL, OK]` with `max_retries=1` → success,
  same connection (assert no reconnect).
- **Retries exhausted:** `[FAIL, FAIL]` with `max_retries=1` → `FAILED` at that stage, no restart.
- **Crash-on-failure restarts:** a `crashes_on_failure=True` stage fails on connection #1, second
  connection scripted all-`OK` → success with `restarts_used=1` (assert `session.reconnect` fired).
- **Connection drop mid-chain:** stage scripted `DROP` → restart; assert reconnect and, if the
  fresh connection succeeds, overall success.
- **Restart budget exhausted:** every connection drops/crashes, `max_restarts=1` → `FAILED` with
  "restart budget exhausted".
