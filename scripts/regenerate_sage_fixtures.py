#!/usr/bin/env python3
"""Deterministically reparse committed Sage raw output and refresh checksums."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from backgammon_engine_kit.codec import request_from_dict
from backgammon_engine_kit.models import RawSource
from backgammon_engine_kit.sage.parser import SageJsonParser
from backgammon_engine_kit.serialization import canonical_json


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "sage" / "1.2.20260706"


def generated(bundle):
    request = request_from_dict(json.loads((bundle / "request.json").read_text(encoding="utf-8")))
    execution = json.loads((bundle / "execution.json").read_text(encoding="utf-8"))
    raw = RawSource.from_output(
        (bundle / "stdout.json").read_text(encoding="utf-8"),
        captured_at=execution["completed_at"],
    )
    result = SageJsonParser().parse(
        request,
        raw,
        started_at=execution["started_at"],
        completed_at=execution["completed_at"],
    )
    normalized = canonical_json(result.to_dict()) + "\n"
    names = sorted(path.name for path in bundle.iterdir() if path.is_file() and path.name != "checksums.sha256")
    values = {}
    for name in names:
        value = normalized.encode("utf-8") if name == "normalized-result.json" else (bundle / name).read_bytes()
        values[name] = hashlib.sha256(value).hexdigest()
    checksum_text = "".join("{}  {}\n".format(values[name], name) for name in names)
    return normalized, checksum_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for bundle in (EVIDENCE / "checker-1ply", EVIDENCE / "cube-1ply"):
        normalized, checksum_text = generated(bundle)
        expected = {
            bundle / "normalized-result.json": normalized,
            bundle / "checksums.sha256": checksum_text,
        }
        for path, value in expected.items():
            current = path.read_text(encoding="utf-8")
            if current != value:
                changed.append(str(path.relative_to(ROOT)))
                if not args.check:
                    path.write_text(value, encoding="utf-8")
    if args.check and changed:
        sys.stderr.write("Sage fixtures require regeneration: {}\n".format(", ".join(changed)))
        return 1
    sys.stdout.write("Sage fixtures are deterministic\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
