"""Test-local writable paths that do not depend on the host temp ACL policy."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

import pytest


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
