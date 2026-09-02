#ifndef HANDLERS_H
#define HANDLERS_H

#include "scenario.h"

/* Serves one accepted connection to completion: reads requests in a loop, dispatches each to
 * the matching handler, and returns when the client closes, a handler says to close (crash,
 * drop, protocol error), or the socket errors. */
void handle_connection(int fd, Scenario *scenario);

#endif /* HANDLERS_H */
