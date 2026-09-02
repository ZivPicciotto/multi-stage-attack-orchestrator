"""Runnable scenarios showing the whole flow, against the in-memory mock or the real C simulator.

Each scenario builds its own device + scripted behavior (mock mode) or points at the matching
scenario JSON file (--tcp mode), runs the real MultiAttackOrchestrator against the real attack
catalog, and prints the outcome. Run with:

    .venv/bin/python -m orchestrator.demo            # in-memory mock
    .venv/bin/python -m orchestrator.demo --tcp       # real C simulator, one subprocess per
                                                       # scenario, a fresh free port each time
    .venv/bin/python -m orchestrator.demo --tcp 9500  # real C simulator, fixed port

Enable INFO logging first (configure_logging) to see the full narrative: stage attempts,
retries, crash-restarts, skips, and extraction — that's the point of this module. Running both
modes back to back and diffing their RESULT:/attempt: lines (ignoring timestamps) is "the seam
held" check described in Simulator/plans/phaseF-scenarios-demo.md.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import socket
import subprocess
import time
from pathlib import Path

from orchestrator.attacks.catalog import CATALOG
from orchestrator.config import ConnectionTarget, OrchestratorConfig
from orchestrator.connection import (
    DROP,
    DeviceConnectionProvider,
    DeviceState,
    MockConnectionProvider,
    ScriptedBehavior,
    TcpConnectionProvider,
)
from orchestrator.logging_config import configure_logging
from orchestrator.models import ExtractionMode, ExtractionRequest, IOSVersion, StageResult
from orchestrator.multi_attack_orchestrator import MultiAttackOrchestrator

logger = logging.getLogger("demo")

TARGET = ConnectionTarget(host="localhost", port=9999)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "Simulator" / "scenarios"
SIMULATOR_BIN = REPO_ROOT / "Simulator" / "simulator"

# Set once in main() from --tcp; None means mock mode. Module-level rather than threaded through
# every scenario function's signature, since it's a single run-wide choice, not per-scenario data.
_tcp_port: int | None = None


def _banner(title: str) -> None:
    # Routed through the logger (not print()) so it interleaves correctly with every other
    # component's log lines under any capture method — mixing print()/stdout with logging's
    # stderr stream reorders unpredictably once output is redirected to a single file.
    logger.info("\n%s\n%s\n%s", "=" * 72, title, "=" * 72)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_listening(port: int, proc: subprocess.Popen, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"simulator exited early (rc={proc.returncode}): {stderr}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"simulator never started listening on port {port}")


@contextlib.contextmanager
def _launch_simulator(scenario_file: str):
    """Launches Simulator/simulator as a subprocess against one scenario file, on a fresh port
    unless --tcp was given a fixed one. Torn down on exit — one subprocess per scenario, per
    phase F's recommended simplification (the scenario file is fixed at startup via argv anyway,
    and each demo scenario is already an independent, freshly-constructed run)."""
    port = _tcp_port or _free_port()
    scenario_path = SCENARIOS_DIR / scenario_file
    proc = subprocess.Popen(
        [str(SIMULATOR_BIN), str(port), str(scenario_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_until_listening(port, proc)
        yield ConnectionTarget("127.0.0.1", port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _run_with_provider(
    provider: DeviceConnectionProvider, target: ConnectionTarget, request: ExtractionRequest
) -> None:
    orchestrator = MultiAttackOrchestrator(provider)
    result = orchestrator.run(OrchestratorConfig(target, request))
    logger.info(
        "RESULT: succeeded=%s winning_attack=%s final_phase=%s error=%s",
        result.succeeded,
        result.winning_attack,
        result.final_phase.value,
        result.error,
    )
    for attempt in result.attempts:
        logger.info(
            "  attempt: %-24s status=%-8s failed_stage=%s restarts_used=%d reason=%s",
            attempt.attack_id,
            attempt.status.value,
            attempt.failed_stage,
            attempt.restarts_used,
            attempt.reason,
        )
    if result.extraction is not None:
        logger.info(
            "  extraction: mode=%s succeeded=%s partial=%s files=%d",
            result.extraction.mode.value,
            result.extraction.succeeded,
            result.extraction.partial,
            len(result.extraction.files),
        )


def _run(
    state: DeviceState, behavior: ScriptedBehavior, request: ExtractionRequest, scenario_file: str
) -> None:
    """Runs one scenario against whichever transport --tcp selected. `state`/`behavior` drive
    the mock; `scenario_file` (a faithful JSON translation of the same state/behavior — see
    Simulator/scenarios/) drives the real simulator. Exactly one of the two is actually used per
    call, but every scenario function supplies both so the same call site works either way."""
    if _tcp_port is not None:
        with _launch_simulator(scenario_file) as target:
            _run_with_provider(TcpConnectionProvider(), target, request)
    else:
        _run_with_provider(MockConnectionProvider(state, behavior), TARGET, request)


# A device compatible with all three original catalog attacks: BOOTROM_CHAIN (highest ranked,
# p=0.68), then KERNEL_CHAIN (p=0.54), then PASSCODE_CHAIN (p=0.40).
def _all_compatible_device(filesystem: dict[str, bytes] | None = None) -> DeviceState:
    return DeviceState(
        model="iPhone11,8",
        ios_version=IOSVersion(14, 2),
        battery_level=90,
        filesystem=filesystem or {},
    )


def scenario_happy_path() -> None:
    _banner("Scenario 1 — happy path: top-ranked attack (BOOTROM_CHAIN) wins outright")
    state = _all_compatible_device()
    behavior = ScriptedBehavior()  # every stage defaults to success
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK), "01_happy_path.json")


def scenario_retry_then_succeed() -> None:
    _banner("Scenario 2 — a stage fails once, retries in place, succeeds")
    state = _all_compatible_device()
    # "bootrom" has max_retries=1: script one clean miss, then a hit.
    behavior = ScriptedBehavior(
        stage_events={"bootrom": [StageResult.fail("timing window missed"), StageResult.ok()]}
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK), "02_retry_then_succeed.json")


def scenario_crash_then_restart() -> None:
    _banner("Scenario 3 — a stage crashes the device; whole chain restarts on a fresh connection")
    # iOS 15.0 is above BOOTROM_CHAIN's ceiling (14.8), so KERNEL_CHAIN is the only candidate.
    state = DeviceState(model="iPhone12,1", ios_version=IOSVersion(15, 0), battery_level=90)
    behavior = ScriptedBehavior(
        stage_events={"kernel_rw": [StageResult.crash("kernel panic"), StageResult.ok()]}
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK), "03_crash_then_restart.json")


def scenario_connection_drop_then_restart() -> None:
    _banner("Scenario 3b — the connection drops mid-chain; treated like a crash, restarts")
    state = _all_compatible_device()
    behavior = ScriptedBehavior(stage_events={"payload": [DROP, StageResult.ok()]})
    _run(
        state,
        behavior,
        ExtractionRequest(ExtractionMode.UNLOCK),
        "04_connection_drop_then_restart.json",
    )


def scenario_fallback_after_failure() -> None:
    _banner("Scenario 4 — top attack exhausts its retries and fails; falls through to the next")
    state = _all_compatible_device()
    behavior = ScriptedBehavior(
        # "payload" has no retries configured: one clean failure ends BOOTROM_CHAIN outright.
        stage_events={"payload": [StageResult.fail("payload rejected")]}
    )
    _run(
        state, behavior, ExtractionRequest(ExtractionMode.UNLOCK), "05_fallback_after_failure.json"
    )


def scenario_state_drift_skip() -> None:
    _banner(
        "Scenario 5 — a failed attempt drains the battery; the next-ranked attack no longer "
        "qualifies at re-check and is SKIPPED, falling through to a third attack"
    )
    state = _all_compatible_device()
    behavior = ScriptedBehavior(
        # BOOTROM_CHAIN fails outright on "payload", but not before draining the battery from
        # 90% to 25% — below KERNEL_CHAIN's min_battery=30 (skipped at re-check) but still above
        # PASSCODE_CHAIN's min_battery=20 (attempted and succeeds).
        stage_events={"payload": [StageResult.fail("payload rejected")]},
        battery_drain={"dfu": 25, "bootrom": 25, "payload": 15},
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK), "06_state_drift_skip.json")


def scenario_all_attacks_fail() -> None:
    _banner("Scenario 6 — every viable attack fails; the run reports total failure")
    state = _all_compatible_device()
    behavior = ScriptedBehavior(
        stage_events={
            "payload": [StageResult.fail("payload rejected")],  # ends BOOTROM_CHAIN
            "kernel_rw": [StageResult.fail("info leak stale")],  # ends KERNEL_CHAIN
            "bruteforce": [StageResult.fail("passcode attempt limit")],  # ends PASSCODE_CHAIN
        }
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK), "07_all_attacks_fail.json")


def scenario_extraction_modes() -> None:
    _banner("Scenario 7 — extraction modes, after a successful unlock")
    filesystem = {
        "/private/var/mobile/Library/SMS/sms.db": b"<sms data>",
        "/private/var/mobile/Library/Keychain/keychain.plist": b"<keychain data>",
        "/private/var/mobile/Media/DCIM/IMG_0001.jpg": b"<photo bytes>",
    }

    logger.info("--- 7a: single_file ---")
    _run(
        _all_compatible_device(filesystem),
        ScriptedBehavior(),
        ExtractionRequest(
            ExtractionMode.SINGLE_FILE, ("/private/var/mobile/Library/SMS/sms.db",)
        ),
        "08_extraction_modes.json",
    )

    logger.info("--- 7b: multi_files, one path missing on this device (partial) ---")
    _run(
        _all_compatible_device(filesystem),
        ScriptedBehavior(),
        ExtractionRequest(
            ExtractionMode.MULTI_FILES,
            (
                "/private/var/mobile/Library/SMS/sms.db",
                "/private/var/mobile/Library/Keychain/keychain.plist",
                "/private/var/mobile/Library/Notes/notes.db",  # not on this device
            ),
        ),
        "08_extraction_modes.json",
    )

    logger.info("--- 7c: all_files, device reports its own file list ---")
    _run(
        _all_compatible_device(filesystem),
        ScriptedBehavior(),
        ExtractionRequest(ExtractionMode.ALL_FILES),
        "08_extraction_modes.json",
    )

    logger.info("--- 7d: all_files, connection drops mid-pull (partial + error) ---")
    behavior = ScriptedBehavior(
        drop_on_read=frozenset({"/private/var/mobile/Media/DCIM/IMG_0001.jpg"})
    )
    _run(
        _all_compatible_device(filesystem),
        behavior,
        ExtractionRequest(ExtractionMode.ALL_FILES),
        "08_extraction_modes_drop_on_read.json",
    )


def scenario_context_dependency() -> None:
    _banner(
        "Scenario 8 — a stage needs an earlier stage's payload before it will even touch the "
        "device: SingleAttackSharedContext actually being read, not just written"
    )
    # iOS >= 16 keeps this isolated to KEYBAG_CHAIN (+ PASSCODE_CHAIN as an always-viable
    # fallback) — none of the other catalog attacks' iOS ceilings reach this high.
    logger.info("--- 8a: leak returns its payload — the dependent stage reads it and proceeds ---")
    state_a = DeviceState(model="iPhone15,2", ios_version=IOSVersion(17, 0), battery_level=80)
    behavior_a = ScriptedBehavior(
        stage_events={"class_key_leak": [StageResult.ok(payload=b"<leaked class keys>")]}
    )
    _run(
        state_a,
        behavior_a,
        ExtractionRequest(ExtractionMode.UNLOCK),
        "09a_context_dependency_success.json",
    )

    logger.info(
        "--- 8b: leak succeeds but returns no payload — the dependent stage refuses to even "
        "attempt the device, and the run falls through to PASSCODE_CHAIN ---"
    )
    state_b = DeviceState(model="iPhone15,2", ios_version=IOSVersion(17, 0), battery_level=80)
    _run(
        state_b,
        ScriptedBehavior(),
        ExtractionRequest(ExtractionMode.UNLOCK),
        "09b_context_dependency_missing_payload.json",
    )


SCENARIOS = [
    scenario_happy_path,
    scenario_retry_then_succeed,
    scenario_crash_then_restart,
    scenario_connection_drop_then_restart,
    scenario_fallback_after_failure,
    scenario_state_drift_skip,
    scenario_all_attacks_fail,
    scenario_extraction_modes,
    scenario_context_dependency,
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tcp",
        nargs="?",
        type=int,
        const=0,
        default=None,
        metavar="PORT",
        help="run against the real C simulator instead of the in-memory mock. A fresh free "
        "port is picked per scenario unless PORT is given.",
    )
    return parser.parse_args()


def main() -> None:
    global _tcp_port
    configure_logging()
    args = _parse_args()

    if args.tcp is not None:
        if not SIMULATOR_BIN.exists():
            raise SystemExit(
                f"{SIMULATOR_BIN} not found — build it first: cd Simulator && make"
            )
        _tcp_port = args.tcp
        logger.info("demo: running against the real simulator (%s)", SIMULATOR_BIN)
    else:
        logger.info("demo: running against the in-memory mock")

    logger.info("catalog: %s", [f"{a.id} (p={a.overall_probability:.2f})" for a in CATALOG])
    for scenario in SCENARIOS:
        scenario()


if __name__ == "__main__":
    main()
