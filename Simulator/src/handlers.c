/* Wires the frame codec (phase B) to the scenario/device state (phase C): the four request
 * types become real responses, including the crash-then-close and drop-with-no-response
 * behaviors the wire protocol depends on (see Simulator/plans/overview.md).
 */
#include "handlers.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "device_state.h"
#include "frame.h"
#include "protocol_ids.h"

static int write_protocol_error(int fd, const char *msg) {
    frame_write(fd, RES_PROTOCOL_ERROR, (const uint8_t *)msg, (uint32_t)strlen(msg));
    return 1; /* close */
}

/* Copies req's payload into a fixed-size, NUL-terminated buffer. Every payload the wire hands
 * us is length-checked against the buffer before copying — an oversized REQ_RUN_STAGE/READ_FILE
 * is the most realistic way this server could actually crash, unlike a scripted scenario. */
static int copy_payload(char *buf, size_t buf_size, const Frame *req) {
    if (req->length >= buf_size) return -1;
    if (req->length > 0) memcpy(buf, req->payload, req->length);
    buf[req->length] = '\0';
    return 0;
}

static int handle_get_info(int fd, const Scenario *scenario) {
    char buf[128];
    int n = snprintf(buf, sizeof buf, "%s|%d.%d.%d|%d", scenario->device.model,
                      scenario->device.ios_major, scenario->device.ios_minor,
                      scenario->device.ios_patch, scenario->device.battery);
    frame_write(fd, RES_OK, (const uint8_t *)buf, (uint32_t)n);
    return 0; /* stay open */
}

static int handle_run_stage(int fd, Scenario *scenario, const Frame *req) {
    char stage_id[STAGE_ID_MAX];
    if (copy_payload(stage_id, sizeof stage_id, req) != 0) {
        return write_protocol_error(fd, "stage_id too long");
    }

    StageEvent event = scenario_next_stage_event(scenario, stage_id);
    int drain = scenario_battery_drain_for(scenario, stage_id);
    if (drain > 0) device_state_drain_battery(&scenario->device, drain);

    switch (event.outcome) {
        case OUTCOME_OK:
            frame_write(fd, RES_OK,
                        event.payload_len ? (const uint8_t *)event.payload : NULL,
                        (uint32_t)event.payload_len);
            return 0;
        case OUTCOME_FAIL:
            frame_write(fd, RES_FAIL, (const uint8_t *)event.reason, (uint32_t)strlen(event.reason));
            return 0; /* clean failure — connection stays open, client may retry */
        case OUTCOME_CRASH:
            frame_write(fd, RES_CRASH, (const uint8_t *)event.reason, (uint32_t)strlen(event.reason));
            return 1; /* close — the defining crash-then-close behavior */
        case OUTCOME_DROP:
            return 1; /* close with NOTHING written — indistinguishable from a real network drop */
    }
    return 1;
}

static int compare_str(const void *a, const void *b) {
    const char *const *pa = a;
    const char *const *pb = b;
    return strcmp(*pa, *pb);
}

static int handle_list_files(int fd, const Scenario *scenario) {
    size_t n = scenario->device.file_count;
    const char **paths = NULL;
    if (n > 0) {
        paths = malloc(n * sizeof(char *));
        for (size_t i = 0; i < n; i++) paths[i] = scenario->device.files[i].path;
        qsort(paths, n, sizeof(char *), compare_str);
    }

    size_t total = 0;
    for (size_t i = 0; i < n; i++) total += strlen(paths[i]) + (i + 1 < n ? 1 : 0);

    char *buf = total > 0 ? malloc(total) : NULL;
    size_t pos = 0;
    for (size_t i = 0; i < n; i++) {
        size_t len = strlen(paths[i]);
        memcpy(buf + pos, paths[i], len);
        pos += len;
        if (i + 1 < n) buf[pos++] = '\n';
    }
    free(paths);

    frame_write(fd, RES_OK, (const uint8_t *)buf, (uint32_t)pos); /* empty payload = no files */
    free(buf);
    return 0;
}

static int handle_read_file(int fd, Scenario *scenario, const Frame *req) {
    char path[256];
    if (copy_payload(path, sizeof path, req) != 0) {
        return write_protocol_error(fd, "path too long");
    }

    if (scenario_should_drop_on_read(scenario, path)) {
        return 1; /* close with nothing written — same DROP treatment as a stage */
    }

    size_t len;
    const char *content = device_state_read_file(&scenario->device, path, &len);
    if (content == NULL) {
        char reason[300];
        int n = snprintf(reason, sizeof reason, "no such file: '%s'", path);
        frame_write(fd, RES_FILE_ERROR, (const uint8_t *)reason, (uint32_t)n);
        return 0; /* one missing file doesn't end the session */
    }
    frame_write(fd, RES_OK, (const uint8_t *)content, (uint32_t)len);
    return 0;
}

static int dispatch(int fd, const Frame *req, Scenario *scenario) {
    switch (req->type) {
        case REQ_GET_INFO:
            return handle_get_info(fd, scenario);
        case REQ_RUN_STAGE:
            return handle_run_stage(fd, scenario, req);
        case REQ_LIST_FILES:
            return handle_list_files(fd, scenario);
        case REQ_READ_FILE:
            return handle_read_file(fd, scenario, req);
        default:
            return write_protocol_error(fd, "unknown request type");
    }
}

void handle_connection(int fd, Scenario *scenario) {
    for (;;) {
        Frame req;
        int rc = frame_read(fd, &req);
        if (rc != FRAME_OK) return; /* EOF or read error — nothing to send back */

        int should_close = dispatch(fd, &req, scenario);
        frame_free(&req);
        if (should_close) return;
    }
}
