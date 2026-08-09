#!/usr/bin/env python3
"""Resolve and validate the focused gallery output before recursive removal."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_OUTPUT_NAME = "oracle-identifier-comparison"


def validated_output_path(repository: Path, output: str | Path) -> Path:
    if not str(output).strip():
        raise ValueError("gallery output path must not be empty")

    repository = repository.resolve(strict=True)
    artifacts = (repository / "artifacts").resolve(strict=False)
    candidate = Path(output)
    if not candidate.is_absolute():
        candidate = repository / candidate
    candidate = candidate.resolve(strict=False)

    if candidate == Path(candidate.anchor):
        raise ValueError("gallery output path must not be a filesystem root")
    if candidate == repository:
        raise ValueError("gallery output path must not be the repository root")
    if candidate == artifacts:
        raise ValueError("gallery output path must not be the artifacts root")
    try:
        relative = candidate.relative_to(artifacts)
    except ValueError as exc:
        raise ValueError(
            f"gallery output path must be strictly below {artifacts}"
        ) from exc
    if not relative.parts:
        raise ValueError("gallery output path must be below the artifacts root")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("output", nargs="?")
    args = parser.parse_args(argv)
    output = args.output
    if output is None:
        output = str(args.repository / "artifacts" / DEFAULT_OUTPUT_NAME)
    try:
        validated = validated_output_path(args.repository, output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"refusing unsafe gallery output: {exc}\n")
    print(validated.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
