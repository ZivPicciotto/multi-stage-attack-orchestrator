"""Generates the wire-protocol constants shared by the Python framework and the C simulator
from one source of truth: spec.json. Pure stdlib, no dependencies.

Run: python SharedProtocol/generate.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = Path(__file__).resolve().parent / "spec.json"
PYTHON_OUTPUT = (
    REPO_ROOT / "MultiAttackOrchestrator" / "orchestrator" / "shared_protocol" / "wire_protocol.py"
)
C_OUTPUT = REPO_ROOT / "Simulator" / "shared_protocol" / "protocol_ids.h"

GENERATED_NOTICE = "GENERATED FROM SharedProtocol/spec.json — DO NOT EDIT. Run: python SharedProtocol/generate.py"


def _enum_lines(members: dict[str, int], strip_prefix: str) -> list[str]:
    lines = []
    for name, value in members.items():
        member = name[len(strip_prefix):] if name.startswith(strip_prefix) else name
        lines.append(f"    {member} = {value}")
    return lines


def generate_python(spec: dict[str, Any]) -> str:
    frame = spec["frame"]
    lines = [
        f"# {GENERATED_NOTICE}",
        "",
        "from __future__ import annotations",
        "",
        "from enum import IntEnum",
        "",
        "",
        "class RequestType(IntEnum):",
        *_enum_lines(spec["requests"], "REQ_"),
        "",
        "",
        "class ResponseType(IntEnum):",
        *_enum_lines(spec["responses"], "RES_"),
        "",
        "",
        f"FRAME_TYPE_SIZE: int = {frame['type_size_bytes']}",
        f"FRAME_LENGTH_SIZE: int = {frame['length_size_bytes']}",
        f"FRAME_BYTE_ORDER: str = {frame['byte_order']!r}",
        "",
        "# Shared vocabulary, not a contract: nothing enforces catalog stage IDs are drawn from",
        "# this list. It exists so scenario authors and the Part 1 attack catalog don't invent",
        "# near-duplicate names for the same thing.",
        "CANONICAL_STAGE_IDS: tuple[str, ...] = (",
        *(f"    {sid!r}," for sid in spec["canonical_stage_ids"]),
        ")",
        "",
    ]
    return "\n".join(lines)


def generate_c_header(spec: dict[str, Any]) -> str:
    requests: dict[str, int] = spec["requests"]
    responses: dict[str, int] = spec["responses"]
    frame = spec["frame"]
    all_names = list(requests) + list(responses)
    width = max(len(name) for name in all_names)

    lines = [
        f"/* {GENERATED_NOTICE} */",
        "",
        "#ifndef PROTOCOL_IDS_H",
        "#define PROTOCOL_IDS_H",
        "",
    ]
    for name, value in requests.items():
        lines.append(f"#define {name.ljust(width)} 0x{value:02X}")
    lines.append("")
    for name, value in responses.items():
        lines.append(f"#define {name.ljust(width)} 0x{value:02X}")
    lines += [
        "",
        f"#define FRAME_TYPE_SIZE_BYTES   {frame['type_size_bytes']}",
        f"#define FRAME_LENGTH_SIZE_BYTES {frame['length_size_bytes']}",
        "",
        "#endif /* PROTOCOL_IDS_H */",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    spec = json.loads(SPEC_PATH.read_text())

    PYTHON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    C_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PYTHON_OUTPUT.write_text(generate_python(spec))
    C_OUTPUT.write_text(generate_c_header(spec))

    print(f"wrote {PYTHON_OUTPUT.relative_to(REPO_ROOT)}")
    print(f"wrote {C_OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
