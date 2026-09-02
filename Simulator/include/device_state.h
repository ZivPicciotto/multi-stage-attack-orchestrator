#ifndef DEVICE_STATE_H
#define DEVICE_STATE_H

#include <stddef.h>

#define DEVICE_MODEL_MAX 64

typedef struct {
    char *path;
    char *content; /* not NUL-terminated; length is authoritative */
    size_t length;
} FilesystemEntry;

typedef struct {
    char model[DEVICE_MODEL_MAX];
    int ios_major, ios_minor, ios_patch;
    int battery; /* 0..100 */
    FilesystemEntry *files;
    size_t file_count;
} DeviceState;

/* Clamped at 0 — mirrors Part 1's DeviceState.drain_battery. */
void device_state_drain_battery(DeviceState *state, int amount);

/* Returns the file's content and sets *out_len, or NULL if path isn't in the filesystem.
 * A legitimately empty file returns a non-NULL pointer with *out_len == 0 — never conflate the
 * two, the same lesson Part 1's FileResult.succeeded already had to learn. */
const char *device_state_read_file(const DeviceState *state, const char *path, size_t *out_len);

#endif /* DEVICE_STATE_H */
