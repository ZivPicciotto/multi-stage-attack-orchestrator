#ifndef SERVER_H
#define SERVER_H

#include <stdint.h>

#include "scenario.h"

/* Binds `port`, then accepts connections one at a time — single-threaded, blocking. Each
 * connection is served to completion (handle_connection) before the next accept(); the *next*
 * accept() succeeding immediately is exactly what "reconnect after crash" needs. Returns 0 on a
 * clean shutdown (accept() error other than EINTR), 1 on setup failure. */
int server_run(uint16_t port, Scenario *scenario);

#endif /* SERVER_H */
