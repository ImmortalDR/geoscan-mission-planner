"""Safe text/JSON reads for M1 inputs: size cap, BOM, UTF-16."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json

# Soft caps — refuse pathological uploads in hackathon demos.
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_GEOJSON_BYTES = 32 * 1024 * 1024


class InputFileError(ValueError):
    pass


def read_bytes_capped(path: Path, *, max_bytes: int, label: str | None = None) -> bytes:
    label = label or path.name
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputFileError(f"{label}: cannot stat ({exc})") from exc
    if size > max_bytes:
        raise InputFileError(
            f"{label}: file too large ({size} bytes > limit {max_bytes})"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InputFileError(f"{label}: cannot read ({exc})") from exc


def decode_text(data: bytes, *, label: str) -> str:
    """Try UTF-8 (with BOM), UTF-16 variants. Clear error if binary garbage."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise InputFileError(f"{label}: invalid UTF-16") from exc
    # utf-8-sig strips BOM
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # last resort: UTF-16 without BOM (rare)
    try:
        return data.decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise InputFileError(f"{label}: not valid UTF-8/UTF-16 text") from exc


def read_json_file(
    path: Path,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    label: str | None = None,
) -> Any:
    label = label or path.name
    data = read_bytes_capped(path, max_bytes=max_bytes, label=label)
    text = decode_text(data, label=label)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputFileError(f"{label}: invalid JSON ({exc.msg})") from exc
