"""Phase 6: MultiAttackOrchestrator — the full flow, end-to-end against the fake."""

from __future__ import annotations

from orchestrator.config import ConnectionTarget, OrchestratorConfig
from orchestrator.connection import DeviceState, FakeConnectionProvider, ScriptedBehavior
from orchestrator.connection.base import ConnectionLostError, ProtocolError
from orchestrator.models import (
    Attack,
    DeviceCompatibilityReqs,
    ExtractionMode,
    ExtractionRequest,
    IOSVersion,
    OrchestrationPhase,
    SingleStage,
    StageResult,
)
from orchestrator.multi_attack_orchestrator import MultiAttackOrchestrator
from orchestrator.resolver import AttackResolver

TARGET = ConnectionTarget("localhost", 9999)

A_OK = Attack("a-ok", (SingleStage("s", "sA", 0.9),), DeviceCompatibilityReqs())
A_FAILS = Attack("a-fails", (SingleStage("s", "sB", 0.9),), DeviceCompatibilityReqs())
A_NEEDS_BATTERY = Attack(
    "a-needs-battery", (SingleStage("s", "sC", 0.9),), DeviceCompatibilityReqs(min_battery=90)
)


def run(state, behavior, catalog, mode=ExtractionMode.UNLOCK, paths=()):
    provider = FakeConnectionProvider(state, behavior)
    orch = MultiAttackOrchestrator(provider, resolver=AttackResolver(catalog))
    return orch.run(OrchestratorConfig(TARGET, ExtractionRequest(mode, paths)))


class TestMultiAttackOrchestrator:
    def test_no_compatible_attack(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=5)
        result = run(state, ScriptedBehavior(), (A_NEEDS_BATTERY,))
        assert not result.succeeded
        assert result.final_phase is OrchestrationPhase.RESOLVING_ATTACKS
        assert result.attempts == ()

    def test_first_fails_second_wins(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=60)
        behavior = ScriptedBehavior(stage_events={"sB": [StageResult.fail("miss")]})
        result = run(state, behavior, (A_FAILS, A_OK))
        assert result.succeeded and result.winning_attack == "a-ok"
        assert len(result.attempts) == 2
        assert not result.attempts[0].succeeded and result.attempts[1].succeeded

    def test_all_fail(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=60)
        behavior = ScriptedBehavior(
            stage_events={"sA": [StageResult.fail("x")], "sB": [StageResult.fail("y")]}
        )
        result = run(state, behavior, (A_FAILS, A_OK))
        assert not result.succeeded
        assert result.final_phase is OrchestrationPhase.RUNNING_ATTACK
        assert len(result.attempts) == 2

    def test_state_drift_causes_a_skip_that_falls_through(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=95)
        behavior = ScriptedBehavior(
            stage_events={"sB": [StageResult.fail("miss")]}, battery_drain={"sB": 20}
        )
        result = run(state, behavior, (A_FAILS, A_NEEDS_BATTERY, A_OK))
        assert result.succeeded and result.winning_attack == "a-ok"
        assert result.attempts[1].status.value == "skipped"

    def test_extraction_runs_on_success(self):
        state = DeviceState(
            "m", IOSVersion(14, 0), battery_level=60, filesystem={"/a": b"1", "/b": b"2"}
        )
        result = run(state, ScriptedBehavior(), (A_OK,), mode=ExtractionMode.ALL_FILES)
        assert result.succeeded
        assert result.extraction.succeeded and len(result.extraction.files) == 2

    def test_connection_closed_on_success(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=60)
        closed = []

        class TrackedProvider(FakeConnectionProvider):
            def connect(self, target):
                conn = super().connect(target)
                orig_close = conn.close
                conn.close = lambda: (closed.append(True), orig_close())[1]
                return conn

        provider = TrackedProvider(state, ScriptedBehavior())
        orch = MultiAttackOrchestrator(provider, resolver=AttackResolver((A_OK,)))
        result = orch.run(OrchestratorConfig(TARGET, ExtractionRequest(ExtractionMode.UNLOCK)))
        assert result.succeeded and closed == [True]

    def test_connection_closed_on_all_fail(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=60)
        closed = []

        class TrackedProvider(FakeConnectionProvider):
            def connect(self, target):
                conn = super().connect(target)
                orig_close = conn.close
                conn.close = lambda: (closed.append(True), orig_close())[1]
                return conn

        behavior = ScriptedBehavior(stage_events={"sA": [StageResult.fail("x")]})
        provider = TrackedProvider(state, behavior)
        orch = MultiAttackOrchestrator(provider, resolver=AttackResolver((A_OK,)))
        result = orch.run(OrchestratorConfig(TARGET, ExtractionRequest(ExtractionMode.UNLOCK)))
        assert not result.succeeded and closed == [True]

    def test_connection_drop_during_info_gathering(self):
        class DeadConnection:
            def get_device_info(self):
                raise ConnectionLostError("dead on arrival")

            def close(self):
                pass

        class DeadProvider:
            def connect(self, target):
                return DeadConnection()

        orch = MultiAttackOrchestrator(DeadProvider(), resolver=AttackResolver((A_OK,)))
        result = orch.run(OrchestratorConfig(TARGET, ExtractionRequest(ExtractionMode.UNLOCK)))
        assert not result.succeeded
        assert result.final_phase is OrchestrationPhase.GATHERING_INFO

    def test_protocol_desync_is_fatal_not_retried(self):
        state = DeviceState("m", IOSVersion(14, 0), battery_level=60)

        class DesyncConnection:
            def __init__(self, inner):
                self._inner = inner

            def get_device_info(self):
                return self._inner.get_device_info()

            def run_stage(self, stage_id):
                raise ProtocolError("desync")

            def list_files(self):
                return self._inner.list_files()

            def read_file(self, path):
                return self._inner.read_file(path)

            def close(self):
                self._inner.close()

        class DesyncProvider:
            def __init__(self, state):
                self._inner = FakeConnectionProvider(state, ScriptedBehavior())

            def connect(self, target):
                return DesyncConnection(self._inner.connect(target))

        orch = MultiAttackOrchestrator(DesyncProvider(state), resolver=AttackResolver((A_OK,)))
        result = orch.run(OrchestratorConfig(TARGET, ExtractionRequest(ExtractionMode.UNLOCK)))
        assert not result.succeeded
        assert "protocol desync" in result.error
        assert result.final_phase is OrchestrationPhase.RUNNING_ATTACK
