"""Offline verification and replay of committed GNU evidence bundles."""

import hashlib
import json
from pathlib import Path

from ..codec import request_from_dict, result_from_dict
from ..models import RawSource
from ..serialization import ensure_public_safe
from .parser import GnuTextParser


REQUIRED_FILES = frozenset(
    (
        "README.md",
        "checksums.sha256",
        "execution.json",
        "normalized-result.json",
        "request.json",
        "source.json",
        "stderr.txt",
        "stdin.gnubg",
        "stdout.txt",
    )
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksums(bundle):
    checksum_path = bundle / "checksums.sha256"
    entries = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if separator != "  " or not name or "/" in name or "\\" in name:
            raise ValueError("malformed evidence checksum record")
        entries[name] = digest
    if set(entries) != REQUIRED_FILES - {"checksums.sha256"}:
        raise ValueError("evidence checksum inventory is incomplete")
    for name, expected in entries.items():
        if _sha256(bundle / name) != expected:
            raise ValueError("evidence checksum mismatch: {}".format(name))
    return entries


def load_verified_bundle(path, expected_request=None):
    bundle = Path(path)
    if not bundle.is_dir() or {item.name for item in bundle.iterdir() if item.is_file()} != REQUIRED_FILES:
        raise ValueError("fixture bundle has an unexpected file inventory")
    verify_checksums(bundle)
    for item in bundle.iterdir():
        if item.is_file():
            ensure_public_safe(item.read_text(encoding="utf-8"), item.name)
    request = request_from_dict(json.loads((bundle / "request.json").read_text(encoding="utf-8")))
    if expected_request is not None and request != expected_request:
        raise ValueError("fixture request identity does not match requested analysis")
    result = result_from_dict(
        json.loads((bundle / "normalized-result.json").read_text(encoding="utf-8"))
    )
    stdout = (bundle / "stdout.txt").read_text(encoding="utf-8")
    execution = json.loads((bundle / "execution.json").read_text(encoding="utf-8"))
    raw = RawSource.from_output(stdout, captured_at=execution["completed_at"])
    if execution["stdout_sha256"] != raw.content_sha256:
        raise ValueError("execution metadata stdout checksum mismatch")
    if execution["stderr_sha256"] != _sha256(bundle / "stderr.txt"):
        raise ValueError("execution metadata stderr checksum mismatch")
    reparsed = GnuTextParser().parse(
        request,
        raw,
        started_at=execution["started_at"],
        completed_at=execution["completed_at"],
    )
    if reparsed != result:
        raise ValueError("normalized fixture differs from deterministic parser output")
    if not result.matches_request(request):
        raise ValueError("normalized fixture identity differs from its request")
    return result
