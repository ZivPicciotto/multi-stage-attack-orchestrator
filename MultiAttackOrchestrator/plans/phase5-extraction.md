# Phase 5 — Data extraction

**Goal:** once an attack has unlocked the device, pull data off it. Build "read one file" as the
primitive and "extract everything" on top, per the prompt. Handle a connection that dies partway
through the pull.

**Depends on:** phases 1–2. **Unlocks:** phase 6 (the top orchestrator runs this after a win).

**File:** `orchestrator/extraction.py`

---

## Contract

```python
class DataExtractor:
    def extract(self, request: ExtractionRequest,
                connection: DeviceConnection) -> ExtractionOutcome: ...
```

It receives the **already-unlocked** connection (the one the winning attack ran on — see phase 6
on why that specific connection matters) and dispatches on `request.mode`.

## How "extract everything" is honest

`extract_all` = `connection.list_files()` then `read_file` for each path returned. The framework
never hardcodes what's on the device; it asks. This is why phase 2's protocol includes
`LIST_FILES` — without it, "everything" would really mean "a guessed manifest," and the Python and
C sides would silently depend on matching hardcoded knowledge.

## Dispatch

```python
def extract(self, request, connection) -> ExtractionOutcome:
    if request.mode is ExtractionMode.UNLOCK:
        return ExtractionOutcome(mode=request.mode)          # no files; the win was the unlock
    if request.mode is ExtractionMode.SINGLE_FILE:
        return self._read_paths(request.mode, request.paths, connection)
    if request.mode is ExtractionMode.MULTI_FILES:
        return self._read_paths(request.mode, request.paths, connection)
    # ALL_FILES:
    try:
        paths = connection.list_files()
    except ConnectionLostError as e:
        return ExtractionOutcome(mode=request.mode, error=f"list_files failed: {e}")
    return self._read_paths(request.mode, tuple(paths), connection)

def _read_paths(self, mode, paths, connection) -> ExtractionOutcome:
    results = []
    for p in paths:
        try:
            data = connection.read_file(p)
            results.append(FileResult(p, succeeded=True, data=data))
        except RemoteFileError as e:
            results.append(FileResult(p, succeeded=False, error=str(e)))   # per-file miss, keep going
        except ConnectionLostError as e:
            # session died mid-pull: stop, return what we have + the error
            return ExtractionOutcome(mode, tuple(results), error=f"connection lost after "
                                     f"{len(results)}/{len(paths)}: {e}")
    return ExtractionOutcome(mode, tuple(results))
```

## The two failure modes, deliberately different

- **`RemoteFileError` (one file missing / access denied):** recorded as a failed `FileResult`, the
  pull continues. A forensic tool wants the other nine files even if one path doesn't exist.
- **`ConnectionLostError` (the session dies mid-pull):** we stop and return **the files gathered so
  far plus an error**. We do *not* silently reconnect: a fresh connection loses the unlocked state,
  so extraction genuinely cannot resume. Returning partial data + a clear error is the honest
  outcome, and it directly answers the prompt's "connection that drops partway through."

`ExtractionOutcome.partial` is `True` in both the "8 of 10 files" and "dropped after 5" cases, so
callers can distinguish complete / partial / empty without inspecting every `FileResult`.

## `unlock` mode

The attack *is* the unlock. `UNLOCK` mode means the caller only wanted access, not files, so
extraction is a confirming no-op returning an empty, successful outcome. Modeling it as a mode
(rather than "extraction is optional") keeps the top orchestrator's contract uniform: it always
calls `extract(request, conn)` after a win.

## Tests (`test_extraction.py`)

- **single_file:** existing path → one successful `FileResult` with correct bytes.
- **single_file, missing path:** → one failed `FileResult`, `outcome.succeeded is False`.
- **multi_files, partial:** 3 paths, middle one missing → 3 results, `partial is True`,
  `succeeded is False`, the two good ones carry data.
- **all_files:** fake vFS has N files → `list_files` drives N reads, all present → success.
- **mid-extraction drop:** `read_file` scripted to raise `ConnectionLostError` on the 2nd of 4
  paths → outcome has the first result, an `error`, and no reconnect attempt.
- **unlock:** returns empty successful outcome, performs no reads (assert `read_file` not called).
