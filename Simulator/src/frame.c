/* Wire codec: loops over read()/write() rather than assuming one syscall moves the whole frame.
 * This is the most common socket bug in hand-rolled C servers — read()/write() can transfer
 * fewer bytes than requested even when more are coming.
 */
#include "frame.h"

#include <arpa/inet.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Reads exactly n bytes into buf. If allow_eof_at_start is set and the peer closes before any
 * byte arrives, returns FRAME_EOF (a clean end of session); a close after partial bytes always
 * means FRAME_ERROR (something died mid-frame). */
static int read_full(int fd, uint8_t *buf, size_t n, int allow_eof_at_start) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r < 0) {
            if (errno == EINTR) continue;
            return FRAME_ERROR;
        }
        if (r == 0) {
            if (got == 0 && allow_eof_at_start) return FRAME_EOF;
            return FRAME_ERROR;
        }
        got += (size_t)r;
    }
    return FRAME_OK;
}

static int write_full(int fd, const uint8_t *buf, size_t n) {
    size_t sent = 0;
    while (sent < n) {
        ssize_t w = write(fd, buf + sent, n - sent);
        if (w < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        sent += (size_t)w;
    }
    return 0;
}

int frame_read(int fd, Frame *out) {
    uint8_t header[5];
    int rc = read_full(fd, header, sizeof header, 1);
    if (rc != FRAME_OK) return rc;

    out->type = header[0];
    uint32_t length_be;
    memcpy(&length_be, header + 1, 4);
    out->length = ntohl(length_be);
    out->payload = NULL;

    if (out->length == 0) return FRAME_OK;

    out->payload = malloc(out->length);
    if (out->payload == NULL) return FRAME_ERROR;

    rc = read_full(fd, out->payload, out->length, 0);
    if (rc != FRAME_OK) {
        free(out->payload);
        out->payload = NULL;
    }
    return rc;
}

int frame_write(int fd, uint8_t type, const uint8_t *payload, uint32_t length) {
    uint8_t header[5];
    header[0] = type;
    uint32_t length_be = htonl(length);
    memcpy(header + 1, &length_be, 4);

    if (write_full(fd, header, sizeof header) != 0) return -1;
    if (length > 0 && payload != NULL) {
        if (write_full(fd, payload, length) != 0) return -1;
    }
    return 0;
}

void frame_free(Frame *f) {
    free(f->payload);
    f->payload = NULL;
    f->length = 0;
}
