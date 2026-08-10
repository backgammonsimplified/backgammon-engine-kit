#!/usr/bin/env python3
"""Deterministically reparse retained GNU streams without running GNU."""

import hashlib
import json
from pathlib import Path

from backgammon_engine_kit.codec import request_from_dict
from backgammon_engine_kit.gnu.parser import GnuTextParser
from backgammon_engine_kit.models import RawSource
from backgammon_engine_kit.serialization import canonical_json, ensure_public_safe


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regenerate(bundle):
    request = request_from_dict(json.loads((bundle / "request.json").read_text(encoding="utf-8")))
    execution = json.loads((bundle / "execution.json").read_text(encoding="utf-8"))
    stdout = (bundle / "stdout.txt").read_text(encoding="utf-8")
    raw = RawSource.from_output(stdout, captured_at=execution["completed_at"])
    result = GnuTextParser().parse(
        request,
        raw,
        started_at=execution["started_at"],
        completed_at=execution["completed_at"],
    )
    ensure_public_safe(result.to_dict(), "normalized GNU evidence")
    (bundle / "normalized-result.json").write_text(
        canonical_json(result) + "\n", encoding="utf-8"
    )
    entries = []
    for path in sorted(bundle.iterdir(), key=lambda value: value.name):
        if path.is_file() and path.name != "checksums.sha256":
            entries.append("{}  {}".format(digest(path), path.name))
    (bundle / "checksums.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")


def main():
    evidence = ROOT / "evidence" / "gnu" / "1.08.003"
    for name in ("checker-1ply", "cube-1ply"):
        regenerate(evidence / name)


if __name__ == "__main__":
    main()
