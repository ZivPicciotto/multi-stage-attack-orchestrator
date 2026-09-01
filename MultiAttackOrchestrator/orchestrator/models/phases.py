"""The top-level orchestration phases, used for reporting how far a run progressed."""

from enum import Enum


class OrchestrationPhase(Enum):
    CONNECTING = "connecting"
    GATHERING_INFO = "gathering_info"
    RESOLVING_ATTACKS = "resolving_attacks"
    RUNNING_ATTACK = "running_attack"
    EXTRACTING_DATA = "extracting_data"
    DONE = "done"
    FAILED = "failed"
