# Phase D — Request handlers

**Goal:** wire the frame codec (phase B) to the scenario/device state (phase C): the four request
types become real responses, including the crash-then-close and drop-with-no-response behaviors
the wire protocol depends on.

**Depends on:** phases B, C. **Unlocks:** phase E (the Python client has something real to talk to).

**Files:** `Simulator/src/handlers.c`, `Simulator/include/handlers.h`; replaces the phase B stub
in `server.c`'s `handle_connection`.

---

## `handle_connection` — the per-connection loop

```c
void handle_connection(int fd, Scenario *scenario) {
    for (;;) {
        Frame req;
        int rc = frame_read(fd, &req);
        if (rc != 0) return;   // EOF or read error — client closed or a real network drop; nothing to send back

        int should_close = dispatch(fd, &req, scenario);
        frame_free(&req);
        if (should_close) return;
    }
}
```

**Decision — the loop, not a single request-response, is the connection's lifetime.** A session is
a sequence of requests (`GET_INFO`, then possibly several `RUN_STAGE`s, then `LIST_FILES`/`READ_FILE`
calls) over one TCP connection, matching how `DeviceSession` uses one connection object for a whole
attack chain in Part 1. `dispatch` returns whether the connection should close (crash, protocol
error, or the client hanging up).

## `dispatch` — one request, one response

```c
static int dispatch(int fd, const Frame *req, Scenario *scenario) {
    switch (req->type) {
        case REQ_GET_INFO:   return handle_get_info(fd, scenario);
        case REQ_RUN_STAGE:  return handle_run_stage(fd, scenario, req);
        case REQ_LIST_FILES: return handle_list_files(fd, scenario);
        case REQ_READ_FILE:  return handle_read_file(fd, scenario, req);
        default:
            frame_write(fd, RES_PROTOCOL_ERROR, (const uint8_t*)"unknown request type", 21);
            return 1;  // close
    }
}
```

## `handle_get_info`

```c
static int handle_get_info(int fd, const Scenario *scenario) {
    char buf[128];
    int n = snprintf(buf, sizeof buf, "%s|%d.%d.%d|%d",
        scenario->device.model,
        scenario->device.ios_major, scenario->device.ios_minor, scenario->device.ios_patch,
        scenario->device.battery);
    frame_write(fd, RES_OK, (const uint8_t*)buf, (uint32_t)n);
    return 0;  // stay open
}
```

## `handle_run_stage` — the interesting one

```c
static int handle_run_stage(int fd, Scenario *scenario, const Frame *req) {
    char stage_id[64];
    /* copy req->payload (length req->length) into stage_id, NUL-terminated, bounds-checked */

    StageEvent event = scenario_next_stage_event(scenario, stage_id);
    int drain = scenario_battery_drain_for(scenario, stage_id);
    if (drain > 0) device_state_drain_battery(&scenario->device, drain);

    switch (event.outcome) {
        case OUTCOME_OK:
            frame_write(fd, RES_OK, NULL, 0);   // payload support can be added later if a scenario needs it
            return 0;
        case OUTCOME_FAIL:
            frame_write(fd, RES_FAIL, (const uint8_t*)event.reason, strlen(event.reason));
            return 0;   // clean failure — connection stays open, client may retry
        case OUTCOME_CRASH:
            frame_write(fd, RES_CRASH, (const uint8_t*)event.reason, strlen(event.reason));
            return 1;   // close — the defining behavior from the overview's wire-protocol section
        case OUTCOME_DROP:
            return 1;   // close with NOTHING written — indistinguishable from a real network drop
    }
    return 1;
}
```

**Decision — `OUTCOME_DROP` writes nothing at all.** This is the one place the handler
deliberately does *not* call `frame_write`. A real crash mid-write, a cut cable, or a killed
process all look the same to the client: the socket just stops producing bytes. Modeling it as
"skip the write, close the fd" is the simplest way to produce exactly that signature.

## `handle_list_files` / `handle_read_file`

```c
static int handle_list_files(int fd, const Scenario *scenario) {
    /* build a newline-joined buffer from scenario->device.files, sorted by path
       (the phase 1 DataExtractor plan documents all_files as device-reported and order-stable
       enough to be deterministic in tests — sorting here matches that expectation) */
    frame_write(fd, RES_OK, buf, len);
    return 0;
}

static int handle_read_file(int fd, Scenario *scenario, const Frame *req) {
    char path[256];
    /* copy + NUL-terminate req->payload, bounds-checked */

    if (scenario_should_drop_on_read(scenario, path)) {
        return 1;   // close with nothing written — same DROP treatment as a stage
    }
    size_t len;
    const char *content = device_state_read_file(&scenario->device, path, &len);
    if (content == NULL) {
        const char *reason = "no such file";
        frame_write(fd, RES_FILE_ERROR, (const uint8_t*)reason, strlen(reason));
        return 0;   // one missing file doesn't end the session
    }
    frame_write(fd, RES_OK, (const uint8_t*)content, (uint32_t)len);
    return 0;
}
```

## Malformed input handling

Every payload copy into a fixed-size buffer (`stage_id[64]`, `path[256]`) is **length-checked
against `req->length` before copying** — a `REQ_RUN_STAGE` with an absurd length either gets
truncated safely with a `RES_PROTOCOL_ERROR`, or (for a length that's merely "too big for this
buffer but not malicious") gets a plain truncation guard. This is the C-specific corner Part 1's
Python code never had to think about: buffer safety on every payload the wire hands us, since a
malformed or adversarial length field is the single most realistic way this server could crash for
real, as opposed to only in a scripted scenario.

## Tests

Extending the phase B raw-socket harness into a fuller manual test script (or, better, folded
directly into phase E's Python client tests once `TcpDeviceConnection` exists): one connection per
scenario, asserting exact response bytes for `GET_INFO`, a scripted `RUN_STAGE` sequence
(fail-then-ok, crash-then-close), `LIST_FILES` against a known filesystem, `READ_FILE` for an
existing and a missing path, and a `drop_on_read` path producing a bare connection close. These are
effectively phase D's version of Part 1's `test_connection.py` scenarios, run over a real socket
instead of in-process.
