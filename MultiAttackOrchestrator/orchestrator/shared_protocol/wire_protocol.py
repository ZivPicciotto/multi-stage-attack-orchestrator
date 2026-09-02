# GENERATED FROM SharedProtocol/spec.json — DO NOT EDIT. Run: python SharedProtocol/generate.py

from __future__ import annotations

from enum import IntEnum


class RequestType(IntEnum):
    GET_INFO = 1
    RUN_STAGE = 2
    LIST_FILES = 3
    READ_FILE = 4


class ResponseType(IntEnum):
    OK = 129
    FAIL = 130
    CRASH = 131
    FILE_ERROR = 132
    PROTOCOL_ERROR = 133


FRAME_TYPE_SIZE: int = 1
FRAME_LENGTH_SIZE: int = 4
FRAME_BYTE_ORDER: str = 'big'

# Shared vocabulary, not a contract: nothing enforces catalog stage IDs are drawn from
# this list. It exists so scenario authors and the Part 1 attack catalog don't invent
# near-duplicate names for the same thing.
CANONICAL_STAGE_IDS: tuple[str, ...] = (
    'dfu',
    'bootrom',
    'payload',
    'leak',
    'kernel_rw',
    'escalate',
    'pair',
    'bruteforce',
)
