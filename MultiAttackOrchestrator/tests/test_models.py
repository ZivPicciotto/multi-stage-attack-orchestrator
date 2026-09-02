"""Phase 1: pure model types — no I/O, no mock, just data and invariants."""

from __future__ import annotations

import pytest

from orchestrator.models import (
    Attack,
    AttackResult,
    AttackResultType,
    DeviceCompatibilityReqs,
    DeviceInfo,
    ExtractionMode,
    ExtractionOutcome,
    ExtractionRequest,
    FileResult,
    IOSVersion,
    MultiAttackResult,
    OrchestrationPhase,
    SingleAttackSharedContext,
    SingleStage,
    StageResult,
)


class TestIOSVersion:
    def test_parse_full(self):
        assert IOSVersion.parse("15.4.1") == IOSVersion(15, 4, 1)

    def test_parse_partial_defaults_to_zero(self):
        assert IOSVersion.parse("14") == IOSVersion(14, 0, 0)
        assert IOSVersion.parse("14.2") == IOSVersion(14, 2, 0)

    def test_ordering(self):
        assert IOSVersion(15, 4) < IOSVersion(15, 4, 1) < IOSVersion(16, 0)

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError):
            IOSVersion.parse("x.y")

    def test_str(self):
        assert str(IOSVersion(14, 2)) == "14.2.0"


class TestDeviceCompatibilityReqs:
    def test_all_pass(self):
        reqs = DeviceCompatibilityReqs(
            max_ios=IOSVersion(14, 8),
            supported_models=frozenset({"iPhone11,8"}),
            min_battery=10,
        )
        info = DeviceInfo("iPhone11,8", IOSVersion(14, 2), 60)
        assert reqs.matches(info)
        assert reqs.reasons_incompatible(info) == []

    def test_below_min_ios(self):
        reqs = DeviceCompatibilityReqs(min_ios=IOSVersion(14, 0))
        info = DeviceInfo("m", IOSVersion(13, 0), 60)
        assert not reqs.matches(info)
        assert "below minimum" in reqs.reasons_incompatible(info)[0]

    def test_above_max_ios(self):
        reqs = DeviceCompatibilityReqs(max_ios=IOSVersion(14, 8))
        info = DeviceInfo("m", IOSVersion(15, 0), 60)
        assert not reqs.matches(info)
        assert "above maximum" in reqs.reasons_incompatible(info)[0]

    def test_wrong_model(self):
        reqs = DeviceCompatibilityReqs(supported_models=frozenset({"iPhone11,8"}))
        info = DeviceInfo("iPhone12,1", IOSVersion(14, 0), 60)
        assert not reqs.matches(info)

    def test_low_battery(self):
        reqs = DeviceCompatibilityReqs(min_battery=30)
        info = DeviceInfo("m", IOSVersion(14, 0), 5)
        assert not reqs.matches(info)

    def test_multiple_reasons(self):
        reqs = DeviceCompatibilityReqs(min_ios=IOSVersion(15, 0), min_battery=50)
        info = DeviceInfo("m", IOSVersion(14, 0), 5)
        assert len(reqs.reasons_incompatible(info)) == 2


class TestSingleAttackSharedContext:
    def test_get_default(self):
        ctx = SingleAttackSharedContext()
        assert ctx.get("missing") is None
        assert ctx.get("missing", "default") == "default"

    def test_set_and_get(self):
        ctx = SingleAttackSharedContext()
        ctx.set("token", b"abc")
        assert ctx.get("token") == b"abc"
        assert "token" in ctx
        assert "other" not in ctx


class TestExtractionRequest:
    def test_valid_shapes(self):
        ExtractionRequest(ExtractionMode.UNLOCK)
        ExtractionRequest(ExtractionMode.ALL_FILES)
        ExtractionRequest(ExtractionMode.SINGLE_FILE, ("/a",))
        ExtractionRequest(ExtractionMode.MULTI_FILES, ("/a", "/b"))

    @pytest.mark.parametrize(
        "mode,paths",
        [
            (ExtractionMode.SINGLE_FILE, ()),
            (ExtractionMode.SINGLE_FILE, ("/a", "/b")),
            (ExtractionMode.MULTI_FILES, ()),
            (ExtractionMode.UNLOCK, ("/a",)),
            (ExtractionMode.ALL_FILES, ("/a",)),
        ],
    )
    def test_invalid_shapes_rejected(self, mode, paths):
        with pytest.raises(ValueError):
            ExtractionRequest(mode, paths)


class TestStageResult:
    def test_ok(self):
        r = StageResult.ok(b"payload")
        assert r.succeeded and r.payload == b"payload" and not r.crashed

    def test_fail_is_not_crashed(self):
        r = StageResult.fail("missed")
        assert not r.succeeded and not r.crashed and r.reason == "missed"

    def test_crash_is_a_failure_that_crashed(self):
        r = StageResult.crash("panic")
        assert not r.succeeded and r.crashed and r.reason == "panic"


class TestAttack:
    def test_overall_probability_is_product(self):
        a = Attack(
            "a",
            (SingleStage("s1", "s1", 0.9), SingleStage("s2", "s2", 0.5)),
            DeviceCompatibilityReqs(),
        )
        assert a.overall_probability == pytest.approx(0.45)

    def test_single_stage(self):
        a = Attack("a", (SingleStage("s1", "s1", 0.7),), DeviceCompatibilityReqs())
        assert a.overall_probability == pytest.approx(0.7)

    def test_rejects_empty_stages(self):
        with pytest.raises(ValueError):
            Attack("a", (), DeviceCompatibilityReqs())


class TestFileResult:
    def test_succeeded_inferred_from_data_present(self):
        assert FileResult("/a", b"hello").succeeded

    def test_failed_inferred_from_no_data(self):
        assert not FileResult("/a", error="missing").succeeded

    def test_empty_file_is_still_success_not_truthiness(self):
        # b"" is falsy but not None — succeeded must check `is not None`, not truthiness,
        # or a legitimately empty file would be misreported as a failure.
        assert FileResult("/a", b"").succeeded


class TestExtractionOutcome:
    def test_unlock_always_succeeds(self):
        assert ExtractionOutcome(ExtractionMode.UNLOCK).succeeded

    def test_all_succeeded(self):
        o = ExtractionOutcome(
            ExtractionMode.MULTI_FILES,
            (FileResult("/a", b"x"), FileResult("/b", b"y")),
        )
        assert o.succeeded and not o.partial

    def test_some_succeeded_is_partial(self):
        o = ExtractionOutcome(
            ExtractionMode.MULTI_FILES,
            (FileResult("/a", b"x"), FileResult("/b", error="missing")),
        )
        assert not o.succeeded and o.partial

    def test_all_failed_is_not_partial(self):
        o = ExtractionOutcome(
            ExtractionMode.MULTI_FILES,
            (FileResult("/a", error="e1"), FileResult("/b", error="e2")),
        )
        assert not o.succeeded and not o.partial

    def test_dropped_mid_pull_is_partial(self):
        o = ExtractionOutcome(
            ExtractionMode.ALL_FILES, (FileResult("/a", b"x"),), error="lost"
        )
        assert not o.succeeded and o.partial

    def test_empty_all_files_succeeds_vacuously(self):
        assert ExtractionOutcome(ExtractionMode.ALL_FILES, ()).succeeded


class TestAttackResult:
    def test_success(self):
        r = AttackResult.success("a", restarts_used=2)
        assert r.succeeded and r.status is AttackResultType.SUCCESS and r.restarts_used == 2

    def test_failed(self):
        r = AttackResult.failed("a", "stage2", "boom", restarts_used=1)
        assert not r.succeeded and r.failed_stage == "stage2" and r.reason == "boom"

    def test_skipped(self):
        r = AttackResult.skipped("a", "unfit")
        assert r.status is AttackResultType.SKIPPED and not r.succeeded


class TestMultiAttackResult:
    def test_success_defaults_phase_to_done(self):
        extraction = ExtractionOutcome(ExtractionMode.UNLOCK)
        r = MultiAttackResult.success(
            ExtractionMode.UNLOCK, "win", (AttackResult.success("win"),), extraction
        )
        assert r.succeeded and r.final_phase is OrchestrationPhase.DONE and r.winning_attack == "win"

    def test_failure_carries_no_extraction(self):
        r = MultiAttackResult.failure(
            ExtractionMode.UNLOCK, OrchestrationPhase.RESOLVING_ATTACKS, "no attack"
        )
        assert not r.succeeded and r.extraction is None and r.error == "no attack"
