# Phase B — TCP server skeleton and frame codec

**Goal:** a C program that binds a port, accepts connections one at a time, and can correctly
read and write the wire frame from phase A — with no domain logic yet. Every request just gets a
`RES_PROTOCOL_ERROR` echo. This isolates "can we reliably speak the framing" from "does the
simulator behave like a device," so bugs in one don't hide bugs in the other.

**Depends on:** phase A — done (needs `shared_protocol/protocol_ids.h`). **Unlocks:** phases C and D.

**Files:** `Simulator/src/{main,server,frame}.c`, `Simulator/include/frame.h`, `Simulator/Makefile`.

---

## `frame.h` / `frame.c` — the wire codec

```c
typedef struct {
    uint8_t  type;
    uint32_t length;
    uint8_t *payload;   // heap-allocated, owned by the Frame; NULL if length == 0
} Frame;

// Reads exactly one frame from fd, blocking until the full header+payload arrives or the
// connection closes/errors. Returns 0 on success, -1 on EOF/error (caller checks errno/feof
// equivalent via a distinct return code — see below).
int frame_read(int fd, Frame *out);

// Writes exactly one frame to fd, looping over write() until every byte is sent (a single
// write() call is not guaranteed to send everything, even for a moderately sized payload).
int frame_write(int fd, uint8_t type, const uint8_t *payload, uint32_t length);

void frame_free(Frame *f);
```

**Decision — loop over `read()`/`write()`, never assume one call is the whole frame.** This is the
single most common socket bug in hand-rolled C servers: `read()` can return fewer bytes than
requested even when more are coming (especially for anything beyond a few hundred bytes, but
correctness must not depend on payload size). `frame_read` loops until it has the full 5-byte
header, then loops again until it has `length` bytes of payload. Same discipline in `frame_write`.

**Decision — three-way return, not just success/fail.** `frame_read` distinguishes "clean EOF"
(peer closed after a complete prior exchange — normal end of session) from "error mid-read" (peer
vanished partway through a frame — the DROP case) from "success." The C server's own handling
doesn't need to distinguish these internally (both roll into "stop serving this connection"), but
returning distinct codes here matters if we ever add server-side logging of *why* a session ended.

## `server.c` — the accept loop

```c
int server_run(uint16_t port, const Scenario *scenario) {
    int listen_fd = /* socket() + setsockopt(SO_REUSEADDR) + bind() + listen() */;
    for (;;) {
        int client_fd = accept(listen_fd, NULL, NULL);
        if (client_fd < 0) { if (errno == EINTR) continue; break; }
        handle_connection(client_fd, scenario);   // phase D; a stub here returns PROTOCOL_ERROR
        close(client_fd);
    }
    return 0;
}
```

**Decision — single-threaded, blocking, one connection at a time.** Confirmed in the overview: a
deterministic test simulator has no need for concurrent clients, and `select()`/threads would add
real complexity (shared-state locking around the scenario/device state) for a capability nothing
in Part 1 or Part 3 requires. The accept loop simply serves one connection to completion, closes
it, and accepts the next — which is exactly what "reconnect after crash" needs: the *next*
`accept()` succeeds immediately, representing a successful reconnect.

**Decision — `SO_REUSEADDR`.** Without it, restarting the simulator quickly during development
(or between test runs) hits "Address already in use" from the OS holding the port in `TIME_WAIT`.
Standard for any short-lived test server.

## `main.c` — entry point

```c
// Usage: ./simulator <port> <scenario.json>
int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: %s <port> <scenario.json>\n", argv[0]); return 1; }
    Scenario scenario;
    if (scenario_load(argv[2], &scenario) != 0) { return 1; }   // phase C
    return server_run((uint16_t)atoi(argv[1]), &scenario);
}
```

## `Makefile`

```makefile
CC = cc
CFLAGS = -Wall -Wextra -std=c11 -Iinclude -Ithird_party
SRC = src/main.c src/server.c src/frame.c src/device_state.c src/scenario.c src/handlers.c \
      third_party/cJSON.c

simulator: $(SRC)
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -f simulator
```

**Decision — `-Wall -Wextra`, no `-Werror`.** Warnings surface real bugs (signedness, unused
variables) without turning a stray warning into a hard build failure during iteration; can be
tightened later if desired.

## Verifying phase B before phase C/D exist

A short Python script (not part of the pytest suite — a throwaway dev tool) opens a raw socket,
hand-builds a `REQ_GET_INFO` frame using `struct.pack`, sends it, and checks the response comes
back as `RES_PROTOCOL_ERROR` (since no handler exists yet). This confirms framing works correctly
in isolation — full frames arrive intact, length-prefixing round-trips — before any domain logic
exists to obscure a framing bug.
