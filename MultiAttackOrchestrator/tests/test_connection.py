"""Phase 2: the mock device, provider, and session — the scriptable stand-in for Part 2's TCP."""

from __future__ import annotations

import pytest

from orchestrator.connection import (
    DROP,
    ConnectionLostError,
    DeviceState,
    MockConnectionProvider,
    RemoteFileError,
    ScriptedBehavior,
)
from orchestrator.connection.session import DeviceSession
from orchestrator.models import IOSVersion, StageResult
from tests.conftest import make_session


class TestInMemoryDeviceConnection:
    def test_get_device_info_reflects_live_state(self, device_state):
        with make_session(device_state) as session:
            info = session.connection.get_device_info()
            assert info.model == device_state.model
            assert info.battery_level == 90

    def test_unscripted_stage_defaults_to_success(self, device_state):
        with make_session(device_state) as session:
            r = session.connection.run_stage("anything")
            assert r.succeeded

    def test_scripted_queue_pops_in_order(self, device_state):
        behavior = ScriptedBehavior(
            stage_events={"bootrom": [StageResult.fail("miss"), StageResult.ok(b"leak")]}
        )
        with make_session(device_state, behavior) as session:
            r1 = session.connection.run_stage("bootrom")
            assert not r1.succeeded and not r1.crashed
            r2 = session.connection.run_stage("bootrom")
            assert r2.succeeded and r2.payload == b"leak"

    def test_crash_kills_the_connection(self, device_state):
        behavior = ScriptedBehavior(stage_events={"kernel_rw": [StageResult.crash("panic")]})
        with make_session(device_state, behavior) as session:
            r = session.connection.run_stage("kernel_rw")
            assert r.crashed and not device_state.alive
            with pytest.raises(ConnectionLostError):
                session.connection.run_stage("anything")

    def test_drop_raises_and_kills_the_connection(self, device_state):
        behavior = ScriptedBehavior(stage_events={"stage": [DROP]})
        with make_session(device_state, behavior) as session:
            with pytest.raises(ConnectionLostError):
                session.connection.run_stage("stage")
            assert not device_state.alive

    def test_battery_drain_applies_regardless_of_outcome(self, device_state):
        behavior = ScriptedBehavior(
            stage_events={"bruteforce": [StageResult.fail("x")]},
            battery_drain={"bruteforce": 15},
        )
        with make_session(device_state, behavior) as session:
            session.connection.run_stage("bruteforce")
            assert device_state.battery_level == 75

    def test_list_and_read_files(self, device_state):
        device_state.filesystem = {"/a.db": b"hello", "/b.db": b"world"}
        with make_session(device_state) as session:
            assert session.connection.list_files() == ["/a.db", "/b.db"]
            assert session.connection.read_file("/a.db") == b"hello"

    def test_read_missing_file_raises_remote_file_error(self, device_state):
        with make_session(device_state) as session:
            with pytest.raises(RemoteFileError):
                session.connection.read_file("/missing")

    def test_drop_on_read(self, device_state):
        device_state.filesystem = {"/a.db": b"hello"}
        behavior = ScriptedBehavior(drop_on_read=frozenset({"/a.db"}))
        with make_session(device_state, behavior) as session:
            with pytest.raises(ConnectionLostError):
                session.connection.read_file("/a.db")
            assert not device_state.alive


class TestDeviceSession:
    def test_reconnect_revives_a_dead_device_and_preserves_state(self, device_state):
        behavior = ScriptedBehavior(stage_events={"kernel_rw": [StageResult.crash("panic")]})
        with make_session(device_state, behavior) as session:
            session.connection.run_stage("kernel_rw")
            assert not device_state.alive

            session.reconnect()
            assert device_state.alive
            assert session.reconnect_count == 1
            assert session.connection.get_device_info().battery_level == 90

    def test_reconnect_continues_the_same_scripted_queue(self, device_state):
        # The queue is shared across reconnects, so a retry-after-crash continues where it
        # left off rather than restarting the script.
        behavior = ScriptedBehavior(
            stage_events={"kernel_rw": [StageResult.crash("panic"), StageResult.ok()]}
        )
        with make_session(device_state, behavior) as session:
            r1 = session.connection.run_stage("kernel_rw")
            assert r1.crashed
            session.reconnect()
            r2 = session.connection.run_stage("kernel_rw")
            assert r2.succeeded

    def test_connection_property_raises_outside_context(self, device_state):
        provider = MockConnectionProvider(device_state, ScriptedBehavior())
        session = DeviceSession(provider, target=None)
        with pytest.raises(RuntimeError):
            _ = session.connection


class TestMockConnectionProvider:
    def test_hands_out_independent_connections_sharing_state(self, device_state):
        behavior = ScriptedBehavior()
        provider = MockConnectionProvider(device_state, behavior)
        c1 = provider.connect(target=None)
        c2 = provider.connect(target=None)
        assert c1 is not c2
        assert provider.connect_count == 2
