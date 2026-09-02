"""Runnable scenarios showing the whole flow move, against the in-memory mock.

Each scenario builds its own device + scripted behavior, runs the real
MultiAttackOrchestrator against the real attack catalog, and prints the outcome. Run with:

    .venv/bin/python -m orchestrator.demo

Enable INFO logging first (configure_logging) to see the full narrative: stage attempts,
retries, crash-restarts, skips, and extraction — that's the point of this module.
"""

from __future__ import annotations

import logging

from orchestrator.attacks.catalog import CATALOG
from orchestrator.config import ConnectionTarget, OrchestratorConfig
from orchestrator.connection import DROP, DeviceState, MockConnectionProvider, ScriptedBehavior
from orchestrator.logging_config import configure_logging
from orchestrator.models import ExtractionMode, ExtractionRequest, IOSVersion, StageResult
from orchestrator.multi_attack_orchestrator import MultiAttackOrchestrator

logger = logging.getLogger("demo")

TARGET = ConnectionTarget(host="localhost", port=9999)


def _banner(title: str) -> None:
    # Routed through the logger (not print()) so it interleaves correctly with every other
    # component's log lines under any capture method — mixing print()/stdout with logging's
    # stderr stream reorders unpredictably once output is redirected to a single file.
    logger.info("\n%s\n%s\n%s", "=" * 72, title, "=" * 72)


def _run(state: DeviceState, behavior: ScriptedBehavior, request: ExtractionRequest) -> None:
    provider = MockConnectionProvider(state, behavior)
    orchestrator = MultiAttackOrchestrator(provider)
    result = orchestrator.run(OrchestratorConfig(TARGET, request))
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


# A device compatible with all three catalog attacks: BOOTROM_CHAIN (highest ranked, p=0.68),
# then KERNEL_CHAIN (p=0.54), then PASSCODE_CHAIN (p=0.40).
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
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


def scenario_retry_then_succeed() -> None:
    _banner("Scenario 2 — a stage fails once, retries in place, succeeds")
    state = _all_compatible_device()
    # "bootrom" has max_retries=1: script one clean miss, then a hit.
    behavior = ScriptedBehavior(
        stage_events={"bootrom": [StageResult.fail("timing window missed"), StageResult.ok()]}
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


def scenario_crash_then_restart() -> None:
    _banner("Scenario 3 — a stage crashes the device; whole chain restarts on a fresh connection")
    # iOS 15.0 is above BOOTROM_CHAIN's ceiling (14.8), so KERNEL_CHAIN is the only candidate.
    state = DeviceState(model="iPhone12,1", ios_version=IOSVersion(15, 0), battery_level=90)
    behavior = ScriptedBehavior(
        stage_events={"kernel_rw": [StageResult.crash("kernel panic"), StageResult.ok()]}
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


def scenario_connection_drop_then_restart() -> None:
    _banner("Scenario 3b — the connection drops mid-chain; treated like a crash, restarts")
    state = _all_compatible_device()
    behavior = ScriptedBehavior(stage_events={"payload": [DROP, StageResult.ok()]})
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


def scenario_fallback_after_failure() -> None:
    _banner("Scenario 4 — top attack exhausts its retries and fails; falls through to the next")
    state = _all_compatible_device()
    behavior = ScriptedBehavior(
        # "payload" has no retries configured: one clean failure ends BOOTROM_CHAIN outright.
        stage_events={"payload": [StageResult.fail("payload rejected")]}
    )
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


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
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


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
    _run(state, behavior, ExtractionRequest(ExtractionMode.UNLOCK))


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
    )

    logger.info("--- 7c: all_files, device reports its own file list ---")
    _run(_all_compatible_device(filesystem), ScriptedBehavior(), ExtractionRequest(ExtractionMode.ALL_FILES))

    logger.info("--- 7d: all_files, connection drops mid-pull (partial + error) ---")
    behavior = ScriptedBehavior(
        drop_on_read=frozenset({"/private/var/mobile/Media/DCIM/IMG_0001.jpg"})
    )
    _run(_all_compatible_device(filesystem), behavior, ExtractionRequest(ExtractionMode.ALL_FILES))


SCENARIOS = [
    scenario_happy_path,
    scenario_retry_then_succeed,
    scenario_crash_then_restart,
    scenario_connection_drop_then_restart,
    scenario_fallback_after_failure,
    scenario_state_drift_skip,
    scenario_all_attacks_fail,
    scenario_extraction_modes,
]


def main() -> None:
    configure_logging()
    logger.info(
        "catalog: %s", [f"{a.id} (p={a.overall_probability:.2f})" for a in CATALOG]
    )
    for scenario in SCENARIOS:
        scenario()


if __name__ == "__main__":
    main()
