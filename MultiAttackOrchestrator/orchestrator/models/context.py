"""The shared scratchpad threaded through the stages of one attack-chain attempt."""

from __future__ import annotations

from typing import Any


class SingleAttackSharedContext:
    """Mutable, per-chain-attempt store. Stages write named outputs; later stages read them.

    Lives for exactly one chain attempt: on a crash-restart the orchestrator creates a fresh
    one, because the device was reset and any accumulated values are stale.
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._store
