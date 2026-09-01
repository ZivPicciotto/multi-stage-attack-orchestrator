"""Device attributes and the per-attack compatibility requirements checked against them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class IOSVersion:
    """A comparable iOS version. `order=True` makes `<`/`>=` compare (major, minor, patch)."""

    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, text: str) -> IOSVersion:
        parts = text.strip().split(".")
        if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"invalid iOS version: {text!r}")
        nums = [int(p) for p in parts] + [0] * (3 - len(parts))
        return cls(*nums)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class DeviceInfo:
    model: str  # e.g. "iPhone11,8"
    ios_version: IOSVersion
    battery_level: int  # 0..100


@dataclass(frozen=True)
class DeviceCompatibilityReqs:
    min_ios: IOSVersion | None = None
    max_ios: IOSVersion | None = None
    supported_models: frozenset[str] | None = None  # None = any model
    min_battery: int = 0

    def matches(self, info: DeviceInfo) -> bool:
        return not self.reasons_incompatible(info)

    def reasons_incompatible(self, info: DeviceInfo) -> list[str]:
        reasons: list[str] = []
        if self.min_ios is not None and info.ios_version < self.min_ios:
            reasons.append(f"iOS {info.ios_version} below minimum {self.min_ios}")
        if self.max_ios is not None and info.ios_version > self.max_ios:
            reasons.append(f"iOS {info.ios_version} above maximum {self.max_ios}")
        if self.supported_models is not None and info.model not in self.supported_models:
            reasons.append(f"model {info.model} not in supported set")
        if info.battery_level < self.min_battery:
            reasons.append(f"battery {info.battery_level}% below minimum {self.min_battery}%")
        return reasons
