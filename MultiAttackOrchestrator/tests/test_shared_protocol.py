"""Phase A: the shared-protocol codegen — drift-guard against SharedProtocol/spec.json."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE_PATH = REPO_ROOT / "SharedProtocol" / "generate.py"
SPEC_PATH = REPO_ROOT / "SharedProtocol" / "spec.json"


def _load_generate_module() -> ModuleType:
    # generate.py lives outside the orchestrator package (it's shared with the C side), so it's
    # loaded by file path rather than imported normally.
    spec = importlib.util.spec_from_file_location("shared_protocol_generate", GENERATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = _load_generate_module()
SPEC = json.loads(SPEC_PATH.read_text())


class TestCodegenMatchesCommittedOutput:
    """If this fails, someone edited spec.json (or a generated file) without re-running
    `python SharedProtocol/generate.py` — regenerate and commit both outputs together."""

    def test_python_module_matches_committed_output(self):
        committed = generate.PYTHON_OUTPUT.read_text()
        assert generate.generate_python(SPEC) == committed

    def test_c_header_matches_committed_output(self):
        committed = generate.C_OUTPUT.read_text()
        assert generate.generate_c_header(SPEC) == committed


class TestSpecShape:
    def test_requests_and_responses_are_distinct_bytes(self):
        all_values = list(SPEC["requests"].values()) + list(SPEC["responses"].values())
        assert len(all_values) == len(set(all_values))
        assert all(0 <= v <= 255 for v in all_values)

    def test_requests_and_responses_occupy_disjoint_ranges(self):
        # Deliberate: a stray byte read out of sync is immediately recognizable as
        # request-shaped or response-shaped by a human staring at a hex dump.
        assert max(SPEC["requests"].values()) < min(SPEC["responses"].values())


class TestGeneratedPythonModuleIsImportable:
    def test_values_match_spec(self):
        from orchestrator.shared_protocol import RequestType, ResponseType

        for name, value in SPEC["requests"].items():
            assert RequestType[name.removeprefix("REQ_")] == value
        for name, value in SPEC["responses"].items():
            assert ResponseType[name.removeprefix("RES_")] == value


class TestCanonicalStageIdsAreActuallyUsed:
    """CANONICAL_STAGE_IDS is only worth generating if something on each side is actually checked
    against it. catalog.py can't drift silently (it imports StageId's named constants directly --
    a typo there is an ImportError, not a mismatch); this class is what keeps the C-side scenario
    JSON files honest, since JSON data can't import a Python constant the way catalog.py does."""

    def test_catalog_stage_ids_are_all_canonical(self):
        from orchestrator.attacks.catalog import CATALOG

        used = {stage.stage_id for attack in CATALOG for stage in attack.stages}
        assert used <= set(SPEC["canonical_stage_ids"])

    def test_scenario_files_only_reference_canonical_stage_ids(self):
        canonical = set(SPEC["canonical_stage_ids"])
        scenarios_dir = REPO_ROOT / "Simulator" / "scenarios"
        offenders: dict[str, set[str]] = {}
        for path in sorted(scenarios_dir.glob("*.json")):
            data = json.loads(path.read_text())
            used = set(data.get("stages", {})) | set(data.get("battery_drain", {}))
            unknown = used - canonical
            if unknown:
                offenders[path.name] = unknown
        assert not offenders, f"scenario files reference non-canonical stage ids: {offenders}"
