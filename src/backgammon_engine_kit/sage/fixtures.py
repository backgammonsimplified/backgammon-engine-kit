"""Offline verification and replay of committed BGSage evidence bundles."""

import hashlib
import json
from pathlib import Path

from ..codec import request_from_dict, result_from_dict
from ..models import RawSource
from ..serialization import ensure_public_safe
from .parser import SageJsonParser


REQUIRED_FILES = frozenset(
    (
        "README.md",
        "checksums.sha256",
        "configuration.json",
        "execution.json",
        "normalized-result.json",
        "request.json",
        "source.json",
        "stderr.txt",
        "stdin.json",
        "stdout.json",
    )
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksums(bundle):
    bundle = Path(bundle)
    entries = {}
    for line in (bundle / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if separator != "  " or not name or "/" in name or "\\" in name:
            raise ValueError("malformed Sage evidence checksum record")
        entries[name] = digest
    if set(entries) != REQUIRED_FILES - {"checksums.sha256"}:
        raise ValueError("Sage evidence checksum inventory is incomplete")
    for name, expected in entries.items():
        if _sha256(bundle / name) != expected:
            raise ValueError("Sage evidence checksum mismatch: {}".format(name))
    return entries


def load_verified_bundle(path, expected_request=None):
    bundle = Path(path)
    if not bundle.is_dir() or {item.name for item in bundle.iterdir() if item.is_file()} != REQUIRED_FILES:
        raise ValueError("Sage fixture bundle has an unexpected file inventory")
    verify_checksums(bundle)
    for item in bundle.iterdir():
        if item.is_file():
            ensure_public_safe(item.read_text(encoding="utf-8"), item.name)
    request = request_from_dict(json.loads((bundle / "request.json").read_text(encoding="utf-8")))
    if expected_request is not None and request != expected_request:
        raise ValueError("fixture request identity does not match requested analysis")
    result = result_from_dict(json.loads((bundle / "normalized-result.json").read_text(encoding="utf-8")))
    output = (bundle / "stdout.json").read_text(encoding="utf-8")
    execution = json.loads((bundle / "execution.json").read_text(encoding="utf-8"))
    raw = RawSource.from_output(output, captured_at=execution["completed_at"])
    if execution["stdout_sha256"] != raw.content_sha256:
        raise ValueError("execution metadata stdout checksum mismatch")
    if execution["stderr_sha256"] != _sha256(bundle / "stderr.txt"):
        raise ValueError("execution metadata stderr checksum mismatch")
    if execution["stdin_sha256"] != _sha256(bundle / "stdin.json"):
        raise ValueError("execution metadata stdin checksum mismatch")
    reparsed = SageJsonParser().parse(
        request,
        raw,
        started_at=execution["started_at"],
        completed_at=execution["completed_at"],
    )
    if reparsed != result:
        raise ValueError("normalized Sage fixture differs from deterministic parser output")
    if not result.matches_request(request):
        raise ValueError("normalized Sage fixture identity differs from its request")
    return result
