CC = cc
CFLAGS = -Wall -Wextra -std=c11 -Iinclude -Ithird_party -Ishared_protocol
SRC = src/main.c src/server.c src/frame.c src/device_state.c src/scenario.c src/handlers.c \
      third_party/cJSON.c

simulator: $(SRC)
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -f simulator

.PHONY: clean
