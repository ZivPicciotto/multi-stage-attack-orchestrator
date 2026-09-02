#include <stdio.h>
#include <stdlib.h>

#include "scenario.h"
#include "server.h"

/* Usage: ./simulator <port> <scenario.json> */
int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <port> <scenario.json>\n", argv[0]);
        return 1;
    }

    Scenario scenario;
    if (scenario_load(argv[2], &scenario) != 0) {
        return 1;
    }

    int rc = server_run((uint16_t)atoi(argv[1]), &scenario);
    scenario_free(&scenario);
    return rc;
}
