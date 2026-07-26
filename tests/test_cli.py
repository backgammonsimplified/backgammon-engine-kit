import json
import subprocess
import sys
from pathlib import Path

from backgammon_engine_kit.cli import handle

from helpers import request


def test_cli_capabilities_operation():
    output = handle({"operation": "capabilities"})
    assert output["ok"] is True
    assert output["capabilities"]["schema_version"] == "engine-capabilities-v1"


def test_cli_validates_request_and_generates_cache_key():
    payload = request().to_dict()
    validated = handle({"operation": "validate_request", "request": payload})
    keyed = handle({"operation": "cache_key", "request": payload})
    assert validated["request"] == payload
    assert keyed["cache_key"].startswith("analysis-result-v2:")


def test_foreground_cli_has_deterministic_json_io():
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "backgammon_engine_kit"]
    env = {"PYTHONPATH": str(root / "src")}
    payload = '{"operation":"capabilities"}'
    first = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    second = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    assert first.returncode == 0
    assert first.stderr == ""
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["ok"] is True
