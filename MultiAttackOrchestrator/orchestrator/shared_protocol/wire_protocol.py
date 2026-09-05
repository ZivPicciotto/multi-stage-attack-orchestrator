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


class StageId:
    """Canonical stage-id vocabulary. Import and use these -- e.g. StageId.KERNEL_RW --
    instead of writing the bare string, so the Part 1 catalog can't quietly drift from
    this generated source: a rename here becomes a rename (or a clean ImportError) in
    catalog.py, not a silent mismatch nothing catches."""

    DFU = 'dfu'
    BOOTROM = 'bootrom'
    PAYLOAD = 'payload'
    LEAK = 'leak'
    KERNEL_RW = 'kernel_rw'
    ESCALATE = 'escalate'
    PAIR = 'pair'
    BRUTEFORCE = 'bruteforce'
    CLASS_KEY_LEAK = 'class_key_leak'
    KEYBAG_UNWRAP = 'keybag_unwrap'


CANONICAL_STAGE_IDS: tuple[str, ...] = (
    StageId.DFU,
    StageId.BOOTROM,
    StageId.PAYLOAD,
    StageId.LEAK,
    StageId.KERNEL_RW,
    StageId.ESCALATE,
    StageId.PAIR,
    StageId.BRUTEFORCE,
    StageId.CLASS_KEY_LEAK,
    StageId.KEYBAG_UNWRAP,
)
