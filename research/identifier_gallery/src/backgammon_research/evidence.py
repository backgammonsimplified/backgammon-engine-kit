from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from typing import Any

from .models import ExportedPosition

S024_MEMBERS = [
    "gallery.html",
    "case-matrix.csv",
    "case-catalog.json",
    "conversion-results.psv",
    "raw-cli-output.json",
    "renderer-output.json",
    "known-limitations.md",
    "commands.txt",
    "validation-checks.md",
    "result-members.txt",
]


def load_s024_result(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != S024_MEMBERS:
            raise ValueError("unexpected s024 result ZIP members")

        matrix_text = archive.read("case-matrix.csv").decode("utf-8")
        matrix = list(csv.DictReader(matrix_text.splitlines()))
        catalog = json.loads(archive.read("case-catalog.json"))
        raw = json.loads(archive.read("raw-cli-output.json"))
        renderer = json.loads(archive.read("renderer-output.json"))

    return {
        "matrix": matrix,
        "catalog": catalog,
        "raw": raw,
        "renderer": renderer,
    }


def split_exported_position(text: str | None) -> ExportedPosition:
    if not text:
        return ExportedPosition(board="No exported position captured.", details="")

    lines = text.splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if "GNU Backgammon  Position ID:" in line
        ),
        None,
    )
    if start is None:
        return ExportedPosition(board=text.strip(), details="")

    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("Pip counts:")
        ),
        None,
    )
    if end is None:
        end = min(len(lines), start + 18)

    board = "\n".join(lines[start:end]).strip()
    detail_lines = [*lines[:start], *lines[end:]]
    details = "\n".join(detail_lines).strip()
    return ExportedPosition(board=board, details=details)


def renderer_cache(renderer_records: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    for record in renderer_records.values():
        if not isinstance(record, dict):
            continue
        identifier = record.get("input")
        output = record.get("output")
        if identifier and output:
            cache[str(identifier)] = record
    return cache
