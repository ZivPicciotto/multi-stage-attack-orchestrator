# Phase C — Device state and scenario loading

**Goal:** the C equivalents of Part 1's `DeviceState` and `ScriptedBehavior` — loaded once from a
JSON scenario file at startup, then mutated over the simulator's lifetime exactly the way the
Python fake mutates its shared state across reconnects.

**Depends on:** phases A, B. **Unlocks:** phase D (handlers read/mutate this state).

**Files:** `Simulator/third_party/cJSON.{c,h}`, `Simulator/src/{device_state,scenario}.c`,
`Simulator/include/{device_state,scenario}.h`.

---

## Vendoring cJSON

`third_party/cJSON.c` + `cJSON.h`, vendored from the upstream single-file release (MIT license,
license header preserved in the file). No submodule, no package manager — just two files checked
into the repo, compiled directly into `simulator` per the phase B `Makefile`. This was the
confirmed choice over hand-rolling a parser: the interesting engineering in this exercise is the
protocol/networking/state-machine logic, not re-deriving JSON parsing, and cJSON's API (`cJSON_Parse`,
`cJSON_GetObjectItem`, `cJSON_IsString`, etc.) is small enough to read end-to-end in a few minutes
if you want to verify it does nothing surprising.

## `device_state.h` / `device_state.c`

```c
typedef struct {
    char model[64];
    int  ios_major, ios_minor, ios_patch;
    int  battery;              // 0..100
    FilesystemEntry *files;    // linked list or dynamic array: path -> content + length
    size_t file_count;
} DeviceState;

void device_state_drain_battery(DeviceState *state, int amount);  // clamped at 0, mirrors DeviceState.drain_battery
const char *device_state_read_file(const DeviceState *state, const char *path, size_t *out_len);
// returns NULL if not found (-> RES_FILE_ERROR)
```

**Decision — no `alive` flag** (per the overview's "what doesn't need to exist" section): a real
socket's liveness *is* the connection's liveness; there's no separate device-level boolean to
track since a fresh `accept()` always represents a successful reconnect at the transport level.

**Decision — filesystem content is inline text, not a directory of real files.** Matches Part 1's
own demo, whose sample files are placeholder strings (`"<sms data>"`) rather than real binary
fixtures. Keeps the scenario JSON self-contained and diff-able; a real binary/base64 mode would be
a legitimate future extension but isn't needed for this exercise's scope.

## `scenario.h` / `scenario.c`

```c
typedef enum { OUTCOME_OK, OUTCOME_FAIL, OUTCOME_CRASH, OUTCOME_DROP } StageOutcome;

typedef struct {
    StageOutcome outcome;
    char reason[128];     // used for FAIL/CRASH
} StageEvent;

typedef struct {
    char stage_id[64];
    StageEvent *queue;    // dynamic array
    size_t queue_len;
    size_t next_index;    // advances on each RUN_STAGE call — the "consumed queue" pointer
} StageScript;

typedef struct {
    DeviceState device;
    StageScript *stages;       // dynamic array, one per scripted stage_id
    size_t stage_count;
    int  *battery_drain_amounts;   // parallel array to stages, or a separate stage_id -> int map
    char **drop_on_read_paths;
    size_t drop_on_read_count;
} Scenario;

int scenario_load(const char *path, Scenario *out);   // parses via cJSON; returns 0 on success

// Called by the RUN_STAGE handler. Advances the queue for stage_id and returns the next event
// (or a default OK event if stage_id was never scripted or its queue is exhausted) — same
// "unscripted defaults to success, repeatable" rule as ScriptedBehavior.next_stage_event.
StageEvent scenario_next_stage_event(Scenario *scenario, const char *stage_id);

int scenario_should_drop_on_read(const Scenario *scenario, const char *path);
```

**Decision — `next_index` lives on the `Scenario` struct, not reset per connection.** This is the
load-bearing detail that makes "crash → reconnect → succeeds" work: `Scenario` is constructed once
in `main()` and passed by pointer into every accepted connection's handler, so a queue consumed
during connection #1 (e.g. popping a `CRASH` event) is already advanced when connection #2 asks for
the same `stage_id` — it gets the next queued event (`OK`), exactly mirroring
`ScriptedBehavior.stage_events` being a `dict[str, list]` shared across `FakeConnectionProvider`
connects in Part 1.

**Decision — unscripted stage defaults to `OK`.** Same rule as the fake, so a scenario file only
needs to name the stages it wants to behave unusually — everything else "just works," keeping
scenario files short and focused on what's actually being tested.

## Tests

Given the scope, phase C doesn't need a full C unit-test framework — a small `test_scenario.c`
driver (compiled as a separate target, not linked into `simulator`) that loads each of phase F's
scenario JSON files and asserts the parsed structure matches expectations (right battery, right
stage count, first `RUN_STAGE`-equivalent call on a scripted stage returns the expected outcome,
second call after manually advancing `next_index` returns the next one) is enough to catch parsing
and consumption bugs before they're obscured by socket I/O in phase D.
