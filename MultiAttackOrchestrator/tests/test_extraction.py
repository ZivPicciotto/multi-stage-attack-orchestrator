"""Phase 5: DataExtractor — unlock / single / multi / all, and the drop-mid-pull case."""

from __future__ import annotations

from orchestrator.connection import DeviceState, InMemoryDeviceConnection, ScriptedBehavior
from orchestrator.extraction import DataExtractor
from orchestrator.models import ExtractionMode, ExtractionRequest, IOSVersion

extractor = DataExtractor()


def connection(filesystem, drop_on_read=frozenset()):
    state = DeviceState("m", IOSVersion(14, 0), 60, filesystem=filesystem)
    behavior = ScriptedBehavior(drop_on_read=drop_on_read)
    return InMemoryDeviceConnection(state, behavior)


class TestDataExtractor:
    def test_unlock_reads_nothing(self):
        conn = connection({"/a": b"x"})
        outcome = extractor.extract(ExtractionRequest(ExtractionMode.UNLOCK), conn)
        assert outcome.succeeded and outcome.files == ()

    def test_single_file_existing(self):
        conn = connection({"/a": b"hello"})
        outcome = extractor.extract(ExtractionRequest(ExtractionMode.SINGLE_FILE, ("/a",)), conn)
        assert outcome.succeeded
        assert outcome.files[0].data == b"hello"

    def test_single_file_missing(self):
        conn = connection({})
        outcome = extractor.extract(
            ExtractionRequest(ExtractionMode.SINGLE_FILE, ("/missing",)), conn
        )
        assert not outcome.succeeded and not outcome.partial
        assert outcome.files[0].error

    def test_multi_files_partial(self):
        conn = connection({"/a": b"1", "/c": b"3"})
        outcome = extractor.extract(
            ExtractionRequest(ExtractionMode.MULTI_FILES, ("/a", "/b", "/c")), conn
        )
        assert not outcome.succeeded and outcome.partial
        assert [f.succeeded for f in outcome.files] == [True, False, True]

    def test_multi_files_all_failed_is_not_partial(self):
        conn = connection({})
        outcome = extractor.extract(
            ExtractionRequest(ExtractionMode.MULTI_FILES, ("/x", "/y")), conn
        )
        assert not outcome.succeeded and not outcome.partial

    def test_all_files_pulls_everything_the_device_lists(self):
        conn = connection({"/a": b"1", "/b": b"2"})
        outcome = extractor.extract(ExtractionRequest(ExtractionMode.ALL_FILES), conn)
        assert outcome.succeeded and len(outcome.files) == 2

    def test_all_files_on_empty_device_succeeds_vacuously(self):
        conn = connection({})
        outcome = extractor.extract(ExtractionRequest(ExtractionMode.ALL_FILES), conn)
        assert outcome.succeeded and outcome.files == ()

    def test_mid_extraction_drop_returns_partial_plus_error(self):
        conn = connection(
            {"/a": b"1", "/b": b"2", "/c": b"3", "/d": b"4"}, drop_on_read=frozenset({"/b"})
        )
        outcome = extractor.extract(
            ExtractionRequest(ExtractionMode.MULTI_FILES, ("/a", "/b", "/c", "/d")), conn
        )
        assert not outcome.succeeded and outcome.partial
        assert outcome.error is not None
        assert len(outcome.files) == 1  # stopped after the drop; did not attempt /c, /d
