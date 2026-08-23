"""Test-local writable paths that do not depend on the host temp ACL policy."""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def compact_publication_stream_authority(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep publication fixtures complete without rebuilding 5M RNG rows per test."""
    if request.node.module.__name__.rsplit(".", 1)[-1] not in {
        "test_ledger_and_publication", "test_campaign_failure_paths",
    }:
        return

    def content(seed: str, game_number: int, seat: str, roll_count: int) -> bytes:
        material = f"{seed}\0{game_number}\0{seat}\0{roll_count}".encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()
        return f"test-complete-stream-v1,{digest}\r\n".encode("ascii")

    def content_sha256(seed: str, game_number: int, seat: str, roll_count: int) -> str:
        return hashlib.sha256(content(seed, game_number, seat, roll_count)).hexdigest()

    import runner.sage_gnu_campaign.match as match_module
    import tests.sage_gnu_campaign.native_fixtures as fixture_module

    monkeypatch.setattr(match_module, "stream_content", content)
    monkeypatch.setattr(match_module, "stream_sha256", content_sha256)
    monkeypatch.setattr(fixture_module, "stream_content", content)
    monkeypatch.setattr(fixture_module, "stream_sha256", content_sha256)


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    root = Path(__file__).resolve().parents[1] / ".test-tmp"
    root.mkdir(mode=0o755, exist_ok=True)
    node_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    name = f"{node_name}-{uuid.uuid4().hex}"
    path = root / name
    path.mkdir(mode=0o755)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
