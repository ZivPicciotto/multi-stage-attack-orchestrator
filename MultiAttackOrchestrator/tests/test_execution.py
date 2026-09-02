"""Phase 4: SingleAttackOrchestrator — retry in place, restart on crash/drop, give up."""

from __future__ import annotations

from orchestrator.connection import DROP, MockConnectionProvider, ScriptedBehavior
from orchestrator.connection.session import DeviceSession
from orchestrator.execution import SingleAttackOrchestrator
from orchestrator.models import Attack, DeviceCompatibilityReqs, SingleStage, StageResult
from tests.conftest import make_session

orch = SingleAttackOrchestrator()


class _CountingProvider(MockConnectionProvider):
    """Wraps every connection's run_stage so calls survive across a reconnect (a fresh
    connection object, but the same underlying provider) — needed to prove a restart
    re-attempts every stage rather than resuming mid-chain."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage_calls: list[str] = []

    def connect(self, target):
        connection = super().connect(target)
        real_run_stage = connection.run_stage
        connection.run_stage = lambda sid: (self.stage_calls.append(sid), real_run_stage(sid))[1]
        return connection


class TestSingleAttackOrchestrator:
    def test_happy_path(self, device_state):
        attack = Attack(
            "happy",
            (SingleStage("s1", "s1", 0.9), SingleStage("s2", "s2", 0.9)),
            DeviceCompatibilityReqs(),
        )
        with make_session(device_state) as session:
            result = orch.run(attack, session)
        assert result.succeeded and result.restarts_used == 0

    def test_retry_then_succeed_stays_on_same_connection(self, device_state):
        attack = Attack(
            "retry", (SingleStage("s", "sid", 0.9, max_retries=1),), DeviceCompatibilityReqs()
        )
        behavior = ScriptedBehavior(stage_events={"sid": [StageResult.fail("miss"), StageResult.ok()]})
        with make_session(device_state, behavior) as session:
            result = orch.run(attack, session)
            assert session.reconnect_count == 0  # no restart needed
        assert result.succeeded

    def test_retries_exhausted_gives_up(self, device_state):
        attack = Attack(
            "exhaust", (SingleStage("s", "sid", 0.9, max_retries=1),), DeviceCompatibilityReqs()
        )
        behavior = ScriptedBehavior(
            stage_events={"sid": [StageResult.fail("a"), StageResult.fail("b")]}
        )
        with make_session(device_state, behavior) as session:
            result = orch.run(attack, session)
        assert not result.succeeded
        assert result.failed_stage == "s"
        assert result.restarts_used == 0

    def test_crash_restarts_whole_chain_then_succeeds(self, device_state):
        attack = Attack(
            "crash",
            (SingleStage("leak", "leak", 0.9), SingleStage("rw", "rw", 0.7)),
            DeviceCompatibilityReqs(),
            max_restarts=1,
        )
        behavior = ScriptedBehavior(stage_events={"rw": [StageResult.crash("panic"), StageResult.ok()]})
        with make_session(device_state, behavior) as session:
            result = orch.run(attack, session)
            assert session.reconnect_count == 1
        assert result.succeeded and result.restarts_used == 1

    def test_connection_drop_restarts_whole_chain(self, device_state):
        attack = Attack(
            "drop", (SingleStage("s", "sid", 0.9),), DeviceCompatibilityReqs(), max_restarts=1
        )
        behavior = ScriptedBehavior(stage_events={"sid": [DROP, StageResult.ok()]})
        with make_session(device_state, behavior) as session:
            result = orch.run(attack, session)
        assert result.succeeded and result.restarts_used == 1

    def test_restart_budget_exhausted_gives_up(self, device_state):
        attack = Attack(
            "budget", (SingleStage("s", "sid", 0.9),), DeviceCompatibilityReqs(), max_restarts=1
        )
        behavior = ScriptedBehavior(
            stage_events={
                "sid": [StageResult.crash("p1"), StageResult.crash("p2"), StageResult.ok()]
            }
        )
        with make_session(device_state, behavior) as session:
            result = orch.run(attack, session)
        assert not result.succeeded
        assert result.restarts_used == 1
        assert "restart budget exhausted" in result.reason

    def test_restart_reruns_every_stage_from_the_start(self, device_state):
        # A restart must re-attempt "first", not resume from "crashy" — proves the chain
        # restarts from stage 1 rather than continuing where it crashed.
        attack = Attack(
            "ctx",
            (SingleStage("first", "first", 0.9), SingleStage("crashy", "crashy", 0.9)),
            DeviceCompatibilityReqs(),
            max_restarts=1,
        )
        behavior = ScriptedBehavior(
            stage_events={"crashy": [StageResult.crash("panic"), StageResult.ok()]}
        )
        provider = _CountingProvider(device_state, behavior)
        with DeviceSession(provider, target=None) as session:
            result = orch.run(attack, session)
        assert result.succeeded and result.restarts_used == 1
        assert provider.stage_calls.count("first") == 2  # once before the crash, once after restart
