#include "scenario.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"

static char *read_entire_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;

    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return NULL;
    }
    long size = ftell(f);
    if (size < 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return NULL;
    }

    char *buf = malloc((size_t)size + 1);
    if (!buf) {
        fclose(f);
        return NULL;
    }
    size_t got = fread(buf, 1, (size_t)size, f);
    fclose(f);
    buf[got] = '\0';
    return buf;
}

static StageOutcome parse_outcome(const char *s) {
    if (strcmp(s, "ok") == 0) return OUTCOME_OK;
    if (strcmp(s, "fail") == 0) return OUTCOME_FAIL;
    if (strcmp(s, "crash") == 0) return OUTCOME_CRASH;
    if (strcmp(s, "drop") == 0) return OUTCOME_DROP;
    return OUTCOME_OK; /* unrecognized outcome string -> safest default */
}

/* Scenario JSON names the same stage_id in both "stages" and "battery_drain" independently
 * (e.g. "payload" fails AND drains battery) — stages are keyed by ID so either section can
 * create the entry and the other just fills in the rest of the same one. */
static StageScript *find_or_add_stage(Scenario *out, const char *stage_id) {
    for (size_t i = 0; i < out->stage_count; i++) {
        if (strcmp(out->stages[i].stage_id, stage_id) == 0) return &out->stages[i];
    }
    StageScript *grown = realloc(out->stages, (out->stage_count + 1) * sizeof(StageScript));
    if (!grown) return NULL;
    out->stages = grown;
    StageScript *s = &out->stages[out->stage_count++];
    memset(s, 0, sizeof(*s));
    strncpy(s->stage_id, stage_id, STAGE_ID_MAX - 1);
    return s;
}

static void load_device(const cJSON *root, DeviceState *device) {
    strncpy(device->model, "unknown", DEVICE_MODEL_MAX - 1);
    device->ios_major = device->ios_minor = device->ios_patch = 0;
    device->battery = 100;

    const cJSON *dev = cJSON_GetObjectItemCaseSensitive(root, "device");
    if (!cJSON_IsObject(dev)) return;

    const cJSON *model = cJSON_GetObjectItemCaseSensitive(dev, "model");
    if (cJSON_IsString(model)) strncpy(device->model, model->valuestring, DEVICE_MODEL_MAX - 1);

    const cJSON *ios = cJSON_GetObjectItemCaseSensitive(dev, "ios_version");
    if (cJSON_IsString(ios)) {
        sscanf(ios->valuestring, "%d.%d.%d", &device->ios_major, &device->ios_minor,
               &device->ios_patch);
    }

    const cJSON *battery = cJSON_GetObjectItemCaseSensitive(dev, "battery");
    if (cJSON_IsNumber(battery)) device->battery = battery->valueint;
}

static void load_filesystem(const cJSON *root, DeviceState *device) {
    const cJSON *fs = cJSON_GetObjectItemCaseSensitive(root, "filesystem");
    if (!cJSON_IsObject(fs)) return;

    int count = cJSON_GetArraySize(fs);
    if (count <= 0) return;
    device->files = calloc((size_t)count, sizeof(FilesystemEntry));
    device->file_count = 0;

    const cJSON *entry;
    cJSON_ArrayForEach(entry, fs) {
        if (!cJSON_IsString(entry)) continue;
        FilesystemEntry *fe = &device->files[device->file_count++];
        fe->path = strdup(entry->string);
        fe->length = strlen(entry->valuestring);
        /* malloc at least 1 byte so a legitimately empty file never returns NULL — the same
         * "is-not-None, not truthiness" lesson Part 1's FileResult had to encode. */
        fe->content = malloc(fe->length > 0 ? fe->length : 1);
        memcpy(fe->content, entry->valuestring, fe->length);
    }
}

static void load_stages(const cJSON *root, Scenario *out) {
    const cJSON *stages = cJSON_GetObjectItemCaseSensitive(root, "stages");
    if (!cJSON_IsObject(stages)) return;

    const cJSON *stage_array;
    cJSON_ArrayForEach(stage_array, stages) {
        if (!cJSON_IsArray(stage_array)) continue;
        StageScript *s = find_or_add_stage(out, stage_array->string);
        if (!s) continue;

        int n = cJSON_GetArraySize(stage_array);
        s->queue = n > 0 ? calloc((size_t)n, sizeof(StageEvent)) : NULL;
        s->queue_len = (size_t)n;
        s->next_index = 0;

        size_t i = 0;
        const cJSON *item;
        cJSON_ArrayForEach(item, stage_array) {
            const cJSON *outcome = cJSON_GetObjectItemCaseSensitive(item, "outcome");
            StageEvent *ev = &s->queue[i++];
            ev->outcome = cJSON_IsString(outcome) ? parse_outcome(outcome->valuestring) : OUTCOME_OK;
            const cJSON *reason = cJSON_GetObjectItemCaseSensitive(item, "reason");
            if (cJSON_IsString(reason)) {
                strncpy(ev->reason, reason->valuestring, REASON_MAX - 1);
            } else {
                ev->reason[0] = '\0';
            }
            const cJSON *payload = cJSON_GetObjectItemCaseSensitive(item, "payload");
            if (cJSON_IsString(payload)) {
                strncpy(ev->payload, payload->valuestring, STAGE_PAYLOAD_MAX - 1);
                ev->payload_len = strlen(ev->payload);
            } else {
                ev->payload[0] = '\0';
                ev->payload_len = 0;
            }
        }
    }
}

static void load_battery_drain(const cJSON *root, Scenario *out) {
    const cJSON *drain = cJSON_GetObjectItemCaseSensitive(root, "battery_drain");
    if (!cJSON_IsObject(drain)) return;

    const cJSON *amount;
    cJSON_ArrayForEach(amount, drain) {
        if (!cJSON_IsNumber(amount)) continue;
        StageScript *s = find_or_add_stage(out, amount->string);
        if (s) s->battery_drain = amount->valueint;
    }
}

static void load_drop_on_read(const cJSON *root, Scenario *out) {
    const cJSON *paths = cJSON_GetObjectItemCaseSensitive(root, "drop_on_read");
    if (!cJSON_IsArray(paths)) return;

    int n = cJSON_GetArraySize(paths);
    if (n <= 0) return;
    out->drop_on_read_paths = calloc((size_t)n, sizeof(char *));
    out->drop_on_read_count = 0;

    const cJSON *item;
    cJSON_ArrayForEach(item, paths) {
        if (!cJSON_IsString(item)) continue;
        out->drop_on_read_paths[out->drop_on_read_count++] = strdup(item->valuestring);
    }
}

int scenario_load(const char *path, Scenario *out) {
    memset(out, 0, sizeof(*out));

    char *text = read_entire_file(path);
    if (!text) {
        fprintf(stderr, "scenario_load: cannot read %s\n", path);
        return -1;
    }

    cJSON *root = cJSON_Parse(text);
    free(text);
    if (!root) {
        fprintf(stderr, "scenario_load: invalid JSON in %s\n", path);
        return -1;
    }

    load_device(root, &out->device);
    load_filesystem(root, &out->device);
    load_stages(root, out);
    load_battery_drain(root, out);
    load_drop_on_read(root, out);

    cJSON_Delete(root);
    return 0;
}

void scenario_free(Scenario *scenario) {
    for (size_t i = 0; i < scenario->stage_count; i++) free(scenario->stages[i].queue);
    free(scenario->stages);

    for (size_t i = 0; i < scenario->device.file_count; i++) {
        free(scenario->device.files[i].path);
        free(scenario->device.files[i].content);
    }
    free(scenario->device.files);

    for (size_t i = 0; i < scenario->drop_on_read_count; i++) free(scenario->drop_on_read_paths[i]);
    free(scenario->drop_on_read_paths);

    memset(scenario, 0, sizeof(*scenario));
}

StageEvent scenario_next_stage_event(Scenario *scenario, const char *stage_id) {
    for (size_t i = 0; i < scenario->stage_count; i++) {
        StageScript *s = &scenario->stages[i];
        if (strcmp(s->stage_id, stage_id) != 0) continue;
        if (s->next_index < s->queue_len) {
            return s->queue[s->next_index++];
        }
        break;
    }
    StageEvent ok;
    memset(&ok, 0, sizeof ok);
    ok.outcome = OUTCOME_OK;
    return ok;
}

int scenario_battery_drain_for(const Scenario *scenario, const char *stage_id) {
    for (size_t i = 0; i < scenario->stage_count; i++) {
        if (strcmp(scenario->stages[i].stage_id, stage_id) == 0) {
            return scenario->stages[i].battery_drain;
        }
    }
    return 0;
}

int scenario_should_drop_on_read(const Scenario *scenario, const char *path) {
    for (size_t i = 0; i < scenario->drop_on_read_count; i++) {
        if (strcmp(scenario->drop_on_read_paths[i], path) == 0) return 1;
    }
    return 0;
}
