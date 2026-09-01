"""Phase 3: filtering and ranking. A pure function of (device info, catalog)."""

from __future__ import annotations

from orchestrator.models import Attack, DeviceCompatibilityReqs, DeviceInfo, IOSVersion, SingleStage
from orchestrator.resolver import AttackResolver

HIGH = Attack("high", (SingleStage("s", "s", 0.9),), DeviceCompatibilityReqs())  # p=0.9
MID = Attack("mid", (SingleStage("s", "s", 0.5),), DeviceCompatibilityReqs())  # p=0.5
LOW_BATTERY_ONLY = Attack(
    "low-battery-only", (SingleStage("s", "s", 0.3),), DeviceCompatibilityReqs(min_battery=80)
)
CATALOG = (MID, HIGH, LOW_BATTERY_ONLY)  # deliberately out of rank order


def info(battery=90):
    return DeviceInfo("m", IOSVersion(14, 0), battery)


class TestAttackResolver:
    def test_ranks_by_descending_probability(self):
        resolver = AttackResolver(CATALOG)
        ranked = resolver.resolve(info(battery=90))
        assert [a.id for a in ranked] == ["high", "mid", "low-battery-only"]

    def test_filters_incompatible(self):
        resolver = AttackResolver(CATALOG)
        ranked = resolver.resolve(info(battery=10))
        assert [a.id for a in ranked] == ["high", "mid"]

    def test_no_compatible_attack_returns_empty(self):
        only_high_battery = Attack(
            "only", (SingleStage("s", "s", 0.9),), DeviceCompatibilityReqs(min_battery=99)
        )
        resolver = AttackResolver((only_high_battery,))
        assert resolver.resolve(info(battery=10)) == []

    def test_tie_breaks_by_fewer_stages_then_id(self):
        two_stage = Attack(
            "z-two-stage",
            (SingleStage("a", "a", 0.7), SingleStage("b", "b", 1.0)),
            DeviceCompatibilityReqs(),
        )  # p=0.7, 2 stages
        one_stage = Attack(
            "a-one-stage", (SingleStage("a", "a", 0.7),), DeviceCompatibilityReqs()
        )  # p=0.7, 1 stage — same probability, fewer stages wins despite id sorting later
        resolver = AttackResolver((two_stage, one_stage))
        ranked = resolver.resolve(info())
        assert [a.id for a in ranked] == ["a-one-stage", "z-two-stage"]

    def test_battery_drift_changes_the_viable_set(self):
        resolver = AttackResolver(CATALOG)
        assert "low-battery-only" not in [a.id for a in resolver.resolve(info(battery=50))]
        assert "low-battery-only" in [a.id for a in resolver.resolve(info(battery=85))]
