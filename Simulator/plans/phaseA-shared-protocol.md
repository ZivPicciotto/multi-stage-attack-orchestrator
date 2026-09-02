# Phase A — Shared protocol module

**Goal:** one canonical source of truth for the wire-level opcodes and frame format, so a new
opcode (should we ever need one) is added in exactly one place instead of two.

**Depends on:** nothing new (Part 1 is done). **Unlocks:** phases B and E, which both need the
generated constants.

**Files:** `protocol/spec.json`, `protocol/generate.py`, generated
`MultiAttackOrchestrator/orchestrator/wire_protocol.py` and `Simulator/include/protocol_ids.h`.

---

## `protocol/spec.json`

```json
{
  "frame": { "type_size_bytes": 1, "length_size_bytes": 4, "byte_order": "big" },
  "requests": {
    "REQ_GET_INFO": 1,
    "REQ_RUN_STAGE": 2,
    "REQ_LIST_FILES": 3,
    "REQ_READ_FILE": 4
  },
  "responses": {
    "RES_OK": 129,
    "RES_FAIL": 130,
    "RES_CRASH": 131,
    "RES_FILE_ERROR": 132,
    "RES_PROTOCOL_ERROR": 133
  },
  "canonical_stage_ids": [
    "dfu", "bootrom", "payload", "leak", "kernel_rw", "escalate", "pair", "bruteforce"
  ]
}
```

**Decision — response codes start at 129 (`0x81`), not 5.** Keeping requests and responses in
disjoint byte ranges (`0x01–0x04` vs `0x81–0x85`) means a stray byte read out of sync is
immediately recognizable as "request-shaped" or "response-shaped" by a human staring at a hex
dump — a small but real debugging aid when working with raw sockets in C.

**Decision — `canonical_stage_ids` is documentation, not a contract.** Nothing in the generated
C header or Python module enforces this list; it exists purely so scenario-file authors and the
Part 1 attack catalog draw from one shared vocabulary instead of inventing near-duplicate names
(`"kernel_rw"` vs `"kernelrw"`). A test *may* assert the Part 1 catalog's stage IDs are a subset
of this list (nice-to-have, not required).

## `protocol/generate.py`

Pure stdlib (`json`, string templating — no dependencies, consistent with keeping the codegen tool
itself trivial to trust by reading it). Two output functions:

```python
def generate_python(spec: dict) -> str:
    """Emits an IntEnum for requests, one for responses, plus the frame-format constants."""

def generate_c_header(spec: dict) -> str:
    """Emits #define constants for every opcode, guarded with #ifndef, plus a header comment
    pointing back at protocol/spec.json as the source of truth."""
```

Both outputs get a `# GENERATED FROM protocol/spec.json — DO NOT EDIT` header comment (`//` for
C). Run via `python protocol/generate.py`, writing both files in one invocation so they can never
individually drift out of running the generator.

**Decision — commit the generated files.** Regenerating on every build would mean the C side needs
Python available just to compile, which is backwards for a C project. Instead, the generated files
are checked into the repo like any other source file, and a test (`test_protocol_spec.py` in the
Python suite) re-runs the generator into a temp location and diffs against what's committed —
failing loudly if someone hand-edited a generated file or forgot to regenerate after touching
`spec.json`. This is the standard "generated-and-committed, drift-checked in CI" pattern.

## Sketch of the generated Python module

```python
# GENERATED FROM protocol/spec.json — DO NOT EDIT. Run: python protocol/generate.py

from enum import IntEnum

class RequestType(IntEnum):
    GET_INFO = 1
    RUN_STAGE = 2
    LIST_FILES = 3
    READ_FILE = 4

class ResponseType(IntEnum):
    OK = 129
    FAIL = 130
    CRASH = 131
    FILE_ERROR = 132
    PROTOCOL_ERROR = 133

FRAME_TYPE_SIZE = 1
FRAME_LENGTH_SIZE = 4
FRAME_BYTE_ORDER = "big"
```

## Sketch of the generated C header

```c
/* GENERATED FROM protocol/spec.json — DO NOT EDIT. Run: python protocol/generate.py */
#ifndef PROTOCOL_IDS_H
#define PROTOCOL_IDS_H

#define REQ_GET_INFO    0x01
#define REQ_RUN_STAGE   0x02
#define REQ_LIST_FILES  0x03
#define REQ_READ_FILE   0x04

#define RES_OK              0x81
#define RES_FAIL            0x82
#define RES_CRASH           0x83
#define RES_FILE_ERROR      0x84
#define RES_PROTOCOL_ERROR  0x85

#define FRAME_TYPE_SIZE_BYTES   1
#define FRAME_LENGTH_SIZE_BYTES 4

#endif
```

## Tests

- `generate_python`/`generate_c_header` produce byte-identical output to what's committed
  (`test_protocol_spec.py`, run via a temp-dir regeneration + diff).
- Spot-check: every value in `spec.json`'s `requests`/`responses` is a distinct byte, and requests
  and responses occupy disjoint ranges (guards against a future hand-edit introducing a collision).
