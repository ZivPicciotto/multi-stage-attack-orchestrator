"""Pulls data off an unlocked device: single file, an explicit set, or everything."""

from __future__ import annotations

import logging

from orchestrator.connection.base import ConnectionLostError, DeviceConnection, RemoteFileError
from orchestrator.models.extraction import ExtractionMode, ExtractionRequest
from orchestrator.models.results import ExtractionOutcome, FileResult

logger = logging.getLogger(__name__)


class DataExtractor:
    def extract(
        self, request: ExtractionRequest, connection: DeviceConnection
    ) -> ExtractionOutcome:
        if request.mode is ExtractionMode.UNLOCK:
            logger.info("extraction: unlock-only requested, nothing to pull")
            return ExtractionOutcome(mode=request.mode)

        if request.mode is ExtractionMode.ALL_FILES:
            # Never hardcode what's on the device — ask it. This is what makes "everything" honest.
            try:
                paths = tuple(connection.list_files())
            except ConnectionLostError as e:
                logger.warning("extraction: list_files failed: %s", e)
                return ExtractionOutcome(mode=request.mode, error=f"list_files failed: {e}")
            logger.info("extraction: all_files — device reports %d files", len(paths))
        else:
            paths = request.paths

        return self._read_paths(request.mode, paths, connection)

    def _read_paths(
        self, mode: ExtractionMode, paths: tuple[str, ...], connection: DeviceConnection
    ) -> ExtractionOutcome:
        results: list[FileResult] = []
        for path in paths:
            try:
                data = connection.read_file(path)
            except RemoteFileError as e:
                logger.info("extraction: %r failed: %s", path, e)
                results.append(FileResult(path, succeeded=False, error=str(e)))
                continue  # one file missing doesn't stop the rest
            except ConnectionLostError as e:
                # The session died mid-pull. We do not silently reconnect: a fresh connection
                # loses the unlocked state, so extraction genuinely cannot resume. Return what
                # we gathered plus the error rather than pretending we can continue.
                logger.warning(
                    "extraction: connection lost after %d/%d files: %s",
                    len(results),
                    len(paths),
                    e,
                )
                return ExtractionOutcome(
                    mode,
                    tuple(results),
                    error=f"connection lost after {len(results)}/{len(paths)}: {e}",
                )
            logger.info("extraction: %r OK (%d bytes)", path, len(data))
            results.append(FileResult(path, succeeded=True, data=data))

        outcome = ExtractionOutcome(mode, tuple(results))
        logger.info(
            "extraction: done — %d/%d succeeded (succeeded=%s, partial=%s)",
            sum(1 for f in results if f.succeeded),
            len(results),
            outcome.succeeded,
            outcome.partial,
        )
        return outcome
