"""One-call logging setup for the demo and tests that want visible output."""

from __future__ import annotations

import logging


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)-32s %(message)s",
        datefmt="%H:%M:%S",
    )
