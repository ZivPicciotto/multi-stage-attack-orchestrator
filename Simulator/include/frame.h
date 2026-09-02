#ifndef FRAME_H
#define FRAME_H

#include <stdint.h>

#define FRAME_OK    0
#define FRAME_EOF  -1  /* peer closed cleanly before any bytes of a new frame arrived */
#define FRAME_ERROR -2 /* read/write error, or peer closed partway through a frame */

typedef struct {
    uint8_t type;
    uint32_t length;
    uint8_t *payload; /* heap-allocated, owned by the Frame; NULL if length == 0 */
} Frame;

/* Reads exactly one frame from fd, looping over read() until the full header+payload arrives
 * or the connection closes/errors. Returns FRAME_OK, FRAME_EOF, or FRAME_ERROR. */
int frame_read(int fd, Frame *out);

/* Writes exactly one frame to fd, looping over write() until every byte is sent (a single
 * write() call is not guaranteed to send everything). Returns 0 on success, -1 on error. */
int frame_write(int fd, uint8_t type, const uint8_t *payload, uint32_t length);

void frame_free(Frame *f);

#endif /* FRAME_H */
