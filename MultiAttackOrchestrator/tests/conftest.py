"""Shared fixtures: everything needed to build a scripted device against the fake."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from orchestrator.config import ConnectionTarget
from orchestrator.connection import DeviceState, FakeConnectionProvider, ScriptedBehavior
from orchestrator.connection.session import DeviceSession
from orchestrator.models import IOSVersion


@dataclass(frozen=True)
class _Target:
    """A minimal stand-in for ConnectionTarget — the fake never touches it."""

    host: str = "localhost"
    port: int = 9999


@pytest.fixture
def target() -> _Target:
    return _Target()


@pytest.fixture
def device_state() -> DeviceState:
    return DeviceState(model="iPhone11,8", ios_version=IOSVersion(14, 2), battery_level=90)


def make_session(
    state: DeviceState, behavior: ScriptedBehavior | None = None, target: ConnectionTarget = _Target()
) -> DeviceSession:
    provider = FakeConnectionProvider(state, behavior or ScriptedBehavior())
    return DeviceSession(provider, target)
