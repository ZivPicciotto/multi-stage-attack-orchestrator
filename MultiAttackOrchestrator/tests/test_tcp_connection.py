"""Phase E: TcpDeviceConnection against the real C simulator, over a real socket.

Deliberately mirrors test_connection.py's assertions test-for-test, with the fixture swapped —
if the two are structurally near-duplicates, that's the proof the DeviceConnection seam actually
held between the in-memory mock and a real transport. Where an assertion can't carry over exactly
(the C server has no DeviceState.alive flag; RUN_STAGE's RES_OK never carries a payload in v1),
the comment says why.
"""

from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import ConnectionTarget
from orchestrator.connection import ConnectionLostError, RemoteFileError, TcpConnectionProvider
from orchestrator.connection.session import DeviceSession

REPO_ROOT = Path(__file__).resolve().parents[2]
SIMULATOR_DIR = REPO_ROOT / "Simulator"
SIMULATOR_BIN = SIMULATOR_DIR / "simulator"
HOST = "127.0.0.1"


@pytest.fixture(scope="session", autouse=True)
def build_simulator() -> None:
    subprocess.run(["make", "-C", str(SIMULATOR_DIR)], check=True, capture_output=True, text=True)
    assert SIMULATOR_BIN.exists(), "simulator binary was not built"


def _scenario(
    *,
    model: str = "iPhone11,8",
    ios: str = "14.2.0",
    battery: int = 90,
    filesystem: dict[str, str] | None = None,
    stages: dict[str, list[dict[str, str]]] | None = None,
    battery_drain: dict[str, int] | None = None,
    drop_on_read: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "device": {"model": model, "ios_version": ios, "battery": battery},
        "filesystem": filesystem or {},
        "stages": stages or {},
        "battery_drain": battery_drain or {},
        "drop_on_read": drop_on_read or [],
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_until_listening(port: int, proc: subprocess.Popen, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"simulator exited early (rc={proc.returncode}): {stderr}")
        try:
            with socket.create_connection((HOST, port), timeout=0.2):
                return  # readiness probe only — no request sent, server just sees EOF and closes
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"simulator never started listening on {HOST}:{port}")


@contextlib.contextmanager
def _launch_simulator(scenario: dict[str, Any], tmp_path: Path):
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario))
    port = _free_port()

    proc = subprocess.Popen(
        [str(SIMULATOR_BIN), str(port), str(scenario_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_until_listening(port, proc)
        yield ConnectionTarget(HOST, port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextlib.contextmanager
def make_tcp_session(scenario: dict[str, Any], tmp_path: Path):
    with _launch_simulator(scenario, tmp_path) as target:
        provider = TcpConnectionProvider(timeout=2.0)
        with DeviceSession(provider, target) as session:
            yield session


class TestTcpDeviceConnection:
    def test_get_device_info_reflects_live_state(self, tmp_path):
        with make_tcp_session(_scenario(), tmp_path) as session:
            info = session.connection.get_device_info()
            assert info.model == "iPhone11,8"
            assert info.battery_level == 90

    def test_unscripted_stage_defaults_to_success(self, tmp_path):
        with make_tcp_session(_scenario(), tmp_path) as session:
            r = session.connection.run_stage("anything")
            assert r.succeeded

    def test_scripted_queue_pops_in_order(self, tmp_path):
        scenario = _scenario(
            stages={"bootrom": [{"outcome": "fail", "reason": "miss"}, {"outcome": "ok"}]}
        )
        with make_tcp_session(scenario, tmp_path) as session:
            r1 = session.connection.run_stage("bootrom")
            assert not r1.succeeded and not r1.crashed
            r2 = session.connection.run_stage("bootrom")
            assert r2.succeeded
            # Unlike the mock's StageResult.ok(b"leak"), RUN_STAGE's RES_OK carries no payload
            # in wire protocol v1 (see Simulator/plans/overview.md) — nothing in Part 1's demo
            # catalog needs a stage payload, so this was left out of v1 scope on purpose.

    def test_crash_kills_the_connection(self, tmp_path):
        scenario = _scenario(stages={"kernel_rw": [{"outcome": "crash", "reason": "panic"}]})
        with make_tcp_session(scenario, tmp_path) as session:
            r = session.connection.run_stage("kernel_rw")
            assert r.crashed
            with pytest.raises(ConnectionLostError):
                session.connection.run_stage("anything")

    def test_drop_raises_and_kills_the_connection(self, tmp_path):
        scenario = _scenario(stages={"stage": [{"outcome": "drop"}]})
        with make_tcp_session(scenario, tmp_path) as session:
            with pytest.raises(ConnectionLostError):
                session.connection.run_stage("stage")

    def test_battery_drain_applies_regardless_of_outcome(self, tmp_path):
        scenario = _scenario(
            stages={"bruteforce": [{"outcome": "fail", "reason": "x"}]},
            battery_drain={"bruteforce": 15},
        )
        with make_tcp_session(scenario, tmp_path) as session:
            session.connection.run_stage("bruteforce")
            assert session.connection.get_device_info().battery_level == 75

    def test_list_and_read_files(self, tmp_path):
        scenario = _scenario(filesystem={"/a.db": "hello", "/b.db": "world"})
        with make_tcp_session(scenario, tmp_path) as session:
            assert session.connection.list_files() == ["/a.db", "/b.db"]
            assert session.connection.read_file("/a.db") == b"hello"

    def test_read_missing_file_raises_remote_file_error(self, tmp_path):
        with make_tcp_session(_scenario(), tmp_path) as session:
            with pytest.raises(RemoteFileError):
                session.connection.read_file("/missing")

    def test_drop_on_read(self, tmp_path):
        scenario = _scenario(filesystem={"/a.db": "hello"}, drop_on_read=["/a.db"])
        with make_tcp_session(scenario, tmp_path) as session:
            with pytest.raises(ConnectionLostError):
                session.connection.read_file("/a.db")


class TestDeviceSessionOverTcp:
    # No DeviceState.alive equivalent exists on the C side (see phase C's "what doesn't need to
    # exist" decision) — a real socket's liveness *is* the connection's liveness. So where
    # test_connection.py asserts `not device_state.alive`, these assert the same *externally
    # observable* effect instead: a further call raises ConnectionLostError.

    def test_reconnect_revives_a_dead_connection_and_preserves_state(self, tmp_path):
        scenario = _scenario(stages={"kernel_rw": [{"outcome": "crash", "reason": "panic"}]})
        with make_tcp_session(scenario, tmp_path) as session:
            session.connection.run_stage("kernel_rw")
            session.reconnect()
            assert session.reconnect_count == 1
            assert session.connection.get_device_info().battery_level == 90

    def test_reconnect_continues_the_same_scripted_queue(self, tmp_path):
        # The scenario's queue lives on the long-running server process, not any one socket —
        # so a reconnect naturally continues where it left off, mirroring the mock's shared
        # ScriptedBehavior.stage_events across MockConnectionProvider connects.
        scenario = _scenario(
            stages={"kernel_rw": [{"outcome": "crash", "reason": "panic"}, {"outcome": "ok"}]}
        )
        with make_tcp_session(scenario, tmp_path) as session:
            r1 = session.connection.run_stage("kernel_rw")
            assert r1.crashed
            session.reconnect()
            r2 = session.connection.run_stage("kernel_rw")
            assert r2.succeeded


class TestTcpConnectionProvider:
    def test_hands_out_independent_connections_to_the_same_server(self, tmp_path):
        with _launch_simulator(_scenario(), tmp_path) as target:
            provider = TcpConnectionProvider(timeout=2.0)

            c1 = provider.connect(target)
            assert c1.get_device_info().battery_level == 90
            c1.close()

            c2 = provider.connect(target)
            assert c1 is not c2
            assert c2.get_device_info().battery_level == 90
            c2.close()
