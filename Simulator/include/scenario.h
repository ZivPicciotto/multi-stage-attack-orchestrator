#ifndef SCENARIO_H
#define SCENARIO_H

#include <stddef.h>

#include "device_state.h"

#define STAGE_ID_MAX 64
#define REASON_MAX 128

typedef enum { OUTCOME_OK, OUTCOME_FAIL, OUTCOME_CRASH, OUTCOME_DROP } StageOutcome;

typedef struct {
    StageOutcome outcome;
    char reason[REASON_MAX]; /* used for FAIL/CRASH; empty otherwise */
} StageEvent;

typedef struct {
    char stage_id[STAGE_ID_MAX];
    StageEvent *queue;    /* dynamic array; may be empty if only battery_drain is scripted */
    size_t queue_len;
    size_t next_index;    /* advances on each RUN_STAGE call — the "consumed queue" pointer */
    int battery_drain;    /* 0 if unscripted for this stage */
} StageScript;

typedef struct {
    DeviceState device;
    StageScript *stages; /* dynamic array, one per stage_id named in "stages" or "battery_drain" */
    size_t stage_count;
    char **drop_on_read_paths;
    size_t drop_on_read_count;
} Scenario;

/* Parses `path` via cJSON. Returns 0 on success, -1 on failure (message on stderr). */
int scenario_load(const char *path, Scenario *out);

void scenario_free(Scenario *scenario);

/* Advances the queue for stage_id and returns the next event (or a default OK event if
 * stage_id was never scripted or its queue is exhausted) — same "unscripted defaults to
 * success, repeatable" rule as Part 1's ScriptedBehavior.next_stage_event. next_index lives on
 * the Scenario, not reset per connection, so a queue consumed on one connection continues where
 * it left off on the next — this is what makes "crash -> reconnect -> succeeds" work. */
StageEvent scenario_next_stage_event(Scenario *scenario, const char *stage_id);

int scenario_battery_drain_for(const Scenario *scenario, const char *stage_id);

int scenario_should_drop_on_read(const Scenario *scenario, const char *path);

#endif /* SCENARIO_H */
