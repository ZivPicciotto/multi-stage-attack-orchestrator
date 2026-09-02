"""Wire-protocol constants shared with the C simulator. The real content lives in
wire_protocol.py, which is generated from SharedProtocol/spec.json — this file just re-exports
it, matching the rest of the package's __init__.py convention.
"""

from orchestrator.shared_protocol.wire_protocol import (
    CANONICAL_STAGE_IDS,
    FRAME_BYTE_ORDER,
    FRAME_LENGTH_SIZE,
    FRAME_TYPE_SIZE,
    RequestType,
    ResponseType,
)

__all__ = [
    "RequestType",
    "ResponseType",
    "FRAME_TYPE_SIZE",
    "FRAME_LENGTH_SIZE",
    "FRAME_BYTE_ORDER",
    "CANONICAL_STAGE_IDS",
]
