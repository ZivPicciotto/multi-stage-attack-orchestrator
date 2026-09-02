#include "device_state.h"

#include <string.h>

void device_state_drain_battery(DeviceState *state, int amount) {
    state->battery -= amount;
    if (state->battery < 0) state->battery = 0;
}

const char *device_state_read_file(const DeviceState *state, const char *path, size_t *out_len) {
    for (size_t i = 0; i < state->file_count; i++) {
        if (strcmp(state->files[i].path, path) == 0) {
            *out_len = state->files[i].length;
            return state->files[i].content;
        }
    }
    return NULL;
}
