"""What a caller hands the top orchestrator: where to connect, and what they want once in."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.models.extraction import ExtractionRequest


@dataclass(frozen=True)
class ConnectionTarget:
    host: str
    port: int


@dataclass(frozen=True)
class OrchestratorConfig:
    target: ConnectionTarget
    request: ExtractionRequest
