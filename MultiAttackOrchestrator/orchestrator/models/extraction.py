"""What the caller wants pulled off the device once an attack has unlocked it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExtractionMode(Enum):
    UNLOCK = "unlock"  # only unlock; extract nothing
    SINGLE_FILE = "single_file"
    MULTI_FILES = "multi_files"
    ALL_FILES = "all_files"


@dataclass(frozen=True)
class ExtractionRequest:
    mode: ExtractionMode
    paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Validate at the boundary: this is caller input, so reject bad shapes early.
        if self.mode is ExtractionMode.SINGLE_FILE and len(self.paths) != 1:
            raise ValueError("single_file requires exactly one path")
        if self.mode is ExtractionMode.MULTI_FILES and len(self.paths) < 1:
            raise ValueError("multi_files requires at least one path")
        if self.mode in (ExtractionMode.UNLOCK, ExtractionMode.ALL_FILES) and self.paths:
            raise ValueError(f"{self.mode.value} takes no paths")
