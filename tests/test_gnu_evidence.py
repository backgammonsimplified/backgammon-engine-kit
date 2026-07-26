import json
from pathlib import Path

import pytest

from backgammon_engine_kit.cli import handle
from backgammon_engine_kit.codec import request_from_dict
from backgammon_engine_kit.gnu.fixtures import load_verified_bundle, verify_checksums
from backgammon_engine_kit.gnu.invocation import build_invocation
from backgammon_engine_kit.models import RawSource
from backgammon_engine_kit.serialization import ensure_public_safe, text_sha256


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "gnu" / "1.08.003"


class EvidenceRuntime:
    executable = Path("/opt/gnu/bin/gnubg")
    data_dir = Path("/opt/gnu/share")
    package_data_dir = Path("/opt/gnu/share/gnubg")

    def environment(self):
        return {"HOME": "/dev/null", "LANG": "C", "LC_ALL": "C", "OMP_NUM_THREADS": "1"}


def bundle(name):
    return EVIDENCE / name


def request(name):
    return request_from_dict(json.loads((bundle(name) / "request.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", ("checker-1ply", "cube-1ply"))
def test_fixture_checksums_and_provenance_are_verified(name):
    entries = verify_checksums(bundle(name))
    assert entries["stdout.txt"] == text_sha256(
        (bundle(name) / "stdout.txt").read_text(encoding="utf-8")
    )
    source = json.loads((bundle(name) / "source.json").read_text(encoding="utf-8"))
    assert source["accepted_source"]["validation_status"] == "pass"
    assert len(source["accepted_source"]["source_match_sha256"]) == 64


def test_checker_raw_output_parses_to_committed_normalized_result():
    result = load_verified_bundle(bundle("checker-1ply"))
    decision = result.checker_decision
    assert result.status == "complete"
    assert result.cube_decision is None
    assert decision.actual_evaluation_type == "evaluation"
    assert decision.actual_ply == 1
    assert decision.requested_candidate_count == 8
    assert decision.exported_candidate_count == 8
    assert [candidate.actual_ply for candidate in decision.candidates] == [1, 1, 0, 0, 0, 0, 0, 0]
    assert decision.candidates[0].raw_notation == "8/4 6/4"
    assert decision.candidates[0].equity == 0.025297
    assert decision.candidates[0].is_played_move is None
    assert decision.candidates[0].resulting_position_id is None


def test_cube_raw_output_parses_to_committed_normalized_result():
    result = load_verified_bundle(bundle("cube-1ply"))
    decision = result.cube_decision
    assert result.status == "complete"
    assert result.checker_decision is None
    assert decision.actual_evaluation_type == "evaluation"
    assert decision.actual_ply == 1
    assert decision.gnu_recommendation == "Double, pass"
    assert decision.recommended_action_id == "double-pass"
    assert [action.action_id for action in decision.actions] == [
        "double-pass",
        "double-take",
        "no-double",
    ]
    assert decision.cubeless_equity == 0.639825
    assert decision.cube_efficiency is None
    assert all(action.match_winning_chance is None for action in decision.actions)
    assert all(action.probabilities is None for action in decision.actions)


@pytest.mark.parametrize("name", ("checker-1ply", "cube-1ply"))
def test_fixture_invocation_reconstructs_exact_stdin(name):
    invocation = build_invocation(request(name), EvidenceRuntime())
    assert invocation.stdin_text == (bundle(name) / "stdin.gnubg").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ("checker-1ply", "cube-1ply"))
def test_cli_fixture_mode_returns_verified_result(name):
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
    assert output["analysis"]["result"]["decision_type"] == req.decision_type


def test_fixture_request_identity_mismatch_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        load_verified_bundle(bundle("checker-1ply"), expected_request=request("cube-1ply"))


def test_public_evidence_excludes_private_paths_and_secrets():
    for path in EVIDENCE.rglob("*"):
        if path.is_file():
            ensure_public_safe(path.read_text(encoding="utf-8"), path.name)


def test_raw_stdout_hash_is_the_result_traceability_hash():
    for name in ("checker-1ply", "cube-1ply"):
        result = load_verified_bundle(bundle(name))
        stdout = (bundle(name) / "stdout.txt").read_text(encoding="utf-8")
        assert result.raw_source == RawSource.from_output(stdout, captured_at=result.completed_at)
