import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backgammon_engine_kit.cli import handle
from backgammon_engine_kit.codec import request_from_dict
from backgammon_engine_kit.gnu.fixtures import load_verified_bundle as load_gnu_bundle
from backgammon_engine_kit.sage.fixtures import load_verified_bundle, verify_checksums
from backgammon_engine_kit.sage.invocation import build_invocation
from backgammon_engine_kit.serialization import ensure_public_safe, text_sha256


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "sage" / "1.2.20260706"
GNU_EVIDENCE = ROOT / "evidence" / "gnu" / "1.08.003"


class EvidenceRuntime:
    python_executable = Path("/opt/bgsage/bin/python3")
    protocol_script = Path("/opt/engine-kit/sage-protocol.py")

    def environment(self):
        return {
            "BGBOT_MULTIPLY_THREADS": "1",
            "HOME": "/dev/null",
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONHASHSEED": "0",
        }


def bundle(name):
    return EVIDENCE / name


def request(name):
    return request_from_dict(json.loads((bundle(name) / "request.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", ("checker-1ply", "cube-1ply"))
def test_sage_fixture_checksums_provenance_and_raw_input_are_verified(name):
    entries = verify_checksums(bundle(name))
    assert entries["stdout.json"] == text_sha256(
        (bundle(name) / "stdout.json").read_text(encoding="utf-8")
    )
    source = json.loads((bundle(name) / "source.json").read_text(encoding="utf-8"))
    assert source["accepted_source"]["validation_status"] == "pass"
    assert source["conversion"]["player_perspective"].startswith("player on roll")
    invocation = build_invocation(request(name), EvidenceRuntime())
    assert invocation.stdin_text == (bundle(name) / "stdin.json").read_text(encoding="utf-8")


def test_sage_checker_raw_output_normalizes_observed_candidates():
    result = load_verified_bundle(bundle("checker-1ply"))
    decision = result.checker_decision
    assert result.status == "complete"
    assert result.cube_decision is None
    assert decision.actual_evaluation_type == "neural-network-evaluation"
    assert decision.actual_ply == 1
    assert decision.exported_candidate_count == 18
    assert [candidate.rank for candidate in decision.candidates] == list(range(1, 19))
    assert decision.candidates[0].notation == "8/4 6/4"
    assert decision.candidates[0].equity == 0.00211629387922585
    assert decision.candidates[0].raw_notation is None
    assert decision.candidates[0].probabilities.lose is None
    assert decision.candidates[0].is_played_move is None
    assert decision.candidates[0].resulting_position_id is None


def test_sage_cube_raw_output_normalizes_observed_recommendation():
    result = load_verified_bundle(bundle("cube-1ply"))
    decision = result.cube_decision
    assert result.status == "complete"
    assert result.checker_decision is None
    assert decision.actual_evaluation_type == "neural-network-evaluation"
    assert decision.actual_ply == 1
    assert decision.raw_recommendation == "Double/Pass"
    assert decision.recommended_action_id == "double-pass"
    assert [action.action_id for action in decision.actions] == [
        "no-double",
        "double-take",
        "double-pass",
    ]
    assert decision.cubeless_equity == 0.6658146381378174
    assert decision.probabilities.lose is None
    assert decision.cube_efficiency is None
    assert all(action.match_winning_chance is None for action in decision.actions)
    assert all(action.probabilities is None for action in decision.actions)


@pytest.mark.parametrize("name", ("checker-1ply", "cube-1ply"))
def test_cli_sage_fixture_mode_returns_verified_result(name):
    req = request(name)
    output = handle(
        {
            "operation": "analyze_fixture",
            "request": req.to_dict(),
            "fixture_bundle": str(bundle(name)),
        }
    )
    assert output["ok"] is True
    assert output["analysis"]["cache_outcome"] == "miss"
    assert output["analysis"]["result"]["engine"]["name"] == "sage"


def test_sage_fixture_request_identity_mismatch_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        load_verified_bundle(bundle("checker-1ply"), expected_request=request("cube-1ply"))


def test_sage_fixtures_regenerate_deterministically():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "regenerate_sage_fixtures.py"), "--check"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Sage fixtures are deterministic\n"


def test_sage_public_evidence_excludes_private_paths_and_secrets():
    for path in EVIDENCE.rglob("*"):
        if path.is_file():
            ensure_public_safe(path.read_text(encoding="utf-8"), path.name)


@pytest.mark.parametrize("name", ("checker-1ply", "cube-1ply"))
def test_gnu_and_sage_results_share_top_level_contract(name):
    sage = load_verified_bundle(bundle(name)).to_dict()
    gnu = load_gnu_bundle(GNU_EVIDENCE / name).to_dict()
    assert set(sage) == set(gnu)
    assert sage["schema_version"] == gnu["schema_version"] == "analysis-result-v2"
    assert sage["decision_type"] == gnu["decision_type"]
    assert sage["engine"]["name"] == "sage"
    assert gnu["engine"]["name"] == "gnu"
    assert "native_module_sha256" not in sage["engine"]
    if name == "checker-1ply":
        assert sage["cube_decision"] is None and gnu["cube_decision"] is None
    else:
        assert sage["checker_decision"] is None and gnu["checker_decision"] is None
