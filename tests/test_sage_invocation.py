import json
from pathlib import Path

import pytest

from backgammon_engine_kit.adapters import MalformedRawResponse
from backgammon_engine_kit.cache import cache_key
from backgammon_engine_kit.models import AnalysisRequest, Position, RawSource
from backgammon_engine_kit.process import ProcessOutcome
from backgammon_engine_kit.sage.adapter import SageAdapter
from backgammon_engine_kit.sage.config import (
    SAGE_BEAROFF_SHA256,
    SAGE_ENGINE_VERSION,
    SAGE_MODEL_IDENTITY,
    SAGE_MODEL_NAME,
    SAGE_NATIVE_SHA256,
    SAGE_PROTOCOL_VERSION,
    verified_sage_configuration,
)
from backgammon_engine_kit.sage.invocation import build_invocation
from backgammon_engine_kit.sage.parser import SageJsonParser
from backgammon_engine_kit.serialization import canonical_json
from backgammon_engine_kit.service import AnalysisService


class FakeRuntime:
    python_executable = Path("/opt/bgsage/bin/python3")
    protocol_script = Path("/opt/engine-kit/sage-protocol.py")

    def __init__(self, error=None):
        self.error = error

    def validate_files(self):
        if self.error is not None:
            raise self.error

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


def sage_request(decision_type="checker", setting="1ply", configuration=None):
    position = (
        "4PPgASTgc/ABMA:cAnqAAAAAAAE"
        if decision_type == "checker"
        else "bD3BAQyYd2cEAA:cAngAAAAAAAE"
    )
    return AnalysisRequest(
        position=Position(id=position, format="gnuid"),
        engine="sage",
        analysis_setting=setting,
        decision_type=decision_type,
        dice=(4, 2) if decision_type == "checker" else None,
        configuration=configuration or verified_sage_configuration(),
    )


def identity():
    return {
        "bearoff_sha256": SAGE_BEAROFF_SHA256,
        "engine_version": SAGE_ENGINE_VERSION,
        "model": SAGE_MODEL_NAME,
        "model_identity": SAGE_MODEL_IDENTITY,
        "native_module_sha256": SAGE_NATIVE_SHA256,
        "protocol": SAGE_PROTOCOL_VERSION,
    }


def context(dice):
    return {
        "beaver": False,
        "board": [0] * 26,
        "crawford": False,
        "cube_owner": "centered",
        "cube_value": 1,
        "dice": dice,
        "jacoby": False,
        "match_length": 7,
        "on_roll": "X",
        "opponent_away": 7,
        "opponent_score": 0,
        "player_away": 7,
        "player_score": 0,
    }


def configuration(decision_type):
    return {
        "candidate_generation": "all-legal-moves" if decision_type == "checker" else "not-applicable",
        "cubeful": True,
        "filter_max_moves": 5,
        "filter_threshold": 0.08,
        "include_game_plans": False,
        "include_two_ply_cube_details": False,
        "model": "stage9",
        "parallel_threads": 1,
        "prefilter_threshold": 0.0,
        "seed": 42,
    }


def probabilities():
    return {
        "backgammon_loss": 0.01,
        "backgammon_win": 0.02,
        "gammon_loss": 0.1,
        "gammon_win": 0.2,
        "win": 0.6,
    }


def raw_output(decision_type="checker"):
    request = sage_request(decision_type)
    position_id, match_id = request.position.id.split(":", 1)
    if decision_type == "checker":
        analysis = {
            "board": [0] * 26,
            "candidate_count": 2,
            "candidates": [
                {
                    "board": [rank] + [0] * 25,
                    "cubeless_equity": 0.2 - rank / 100.0,
                    "equity": 0.3 - rank / 100.0,
                    "equity_difference": 0.0 if rank == 1 else -0.01,
                    "eval_level": "1-ply",
                    "move_notation": "8/{}".format(4 - rank),
                    "notation_source": "bgsage.possible_single_die_moves-v1",
                    "probabilities": probabilities(),
                    "rank": rank,
                }
                for rank in (1, 2)
            ],
            "dice": [4, 2],
            "eval_level": "1-ply",
            "type": "checker",
        }
    else:
        analysis = {
            "actions": [
                {"action": "No Double", "equity": 0.2, "output_order": 1},
                {"action": "Double/Take", "equity": 0.3, "output_order": 2},
                {"action": "Double/Pass", "equity": 0.4, "output_order": 3},
            ],
            "cubeless_equity": 0.25,
            "details": None,
            "eval_level": "1-ply",
            "is_beaver": False,
            "optimal_action": "Double/Pass",
            "optimal_equity": 0.4,
            "probabilities": probabilities(),
            "should_double": True,
            "should_take": False,
            "type": "cube",
        }
    return canonical_json(
        {
            "analysis": analysis,
            "configuration": configuration(decision_type),
            "identity": identity(),
            "normalized_input": context([4, 2] if decision_type == "checker" else None),
            "protocol": SAGE_PROTOCOL_VERSION,
            "request_identity": {
                "analysis_setting": "1ply",
                "decision_type": decision_type,
                "match_id": match_id,
                "position_id": position_id,
            },
            "status": "complete",
        }
    ) + "\n"


def test_checker_and_cube_invocations_are_deterministic_and_shell_free():
    for decision_type in ("checker", "cube"):
        first = build_invocation(sage_request(decision_type), FakeRuntime())
        second = build_invocation(sage_request(decision_type), FakeRuntime())
        assert first == second
        assert len(first.argv) == 2
        assert first.public_argv() == ["<BGSAGE_PYTHON>", "<ENGINE_KIT_SAGE_PROTOCOL>"]
        body = json.loads(first.stdin_text)
        assert body["analysis"]["decision_type"] == decision_type
        assert body["analysis"]["analysis_setting"] == "1ply"
        assert body["analysis"]["parallel_threads"] == 1
        assert body["analysis"]["seed"] == 42


def test_sage_checker_parser_preserves_rank_depth_nulls_and_probabilities():
    result = SageJsonParser().parse(sage_request(), RawSource.from_output(raw_output()))
    assert result.status == "complete"
    assert result.cube_decision is None
    assert result.checker_decision.actual_ply == 1
    assert [candidate.rank for candidate in result.checker_decision.candidates] == [1, 2]
    first = result.checker_decision.candidates[0]
    assert first.raw_notation is None
    assert first.notation_source == "bgsage.possible_single_die_moves-v1"
    assert first.probabilities.lose is None
    assert first.is_played_move is None
    assert first.resulting_position_id is None


def test_sage_cube_parser_preserves_raw_and_normalized_recommendation():
    result = SageJsonParser().parse(
        sage_request("cube"), RawSource.from_output(raw_output("cube"))
    )
    assert result.checker_decision is None
    assert result.cube_decision.actual_ply == 1
    assert result.cube_decision.raw_recommendation == "Double/Pass"
    assert result.cube_decision.gnu_recommendation is None
    assert result.cube_decision.recommended_action_id == "double-pass"
    assert result.cube_decision.probabilities.lose is None
    assert result.cube_decision.cube_efficiency is None
    assert all(action.match_winning_chance is None for action in result.cube_decision.actions)


def test_sage_cache_key_is_deterministic_and_differs_from_gnu():
    first = sage_request()
    assert cache_key(first) == cache_key(sage_request())
    from test_gnu_invocation import gnu_request

    assert cache_key(first) != cache_key(gnu_request())


def test_unsupported_setting_and_changed_configuration_fail_before_execution():
    adapter = SageAdapter(FakeRuntime(), process_runner=lambda *args, **kwargs: None)
    unsupported = AnalysisService(adapters={"sage": adapter}).analyze(sage_request(setting="2ply"))
    assert unsupported.result.failure.code == "unsupported_capability"
    valid = verified_sage_configuration()
    changed = valid.__class__(
        engine="sage",
        profile=valid.profile,
        engine_version="changed",
        model_or_weights_identity=valid.model_or_weights_identity,
        invocation_identity=valid.invocation_identity,
        parser_version=valid.parser_version,
        options=valid.options,
    )
    mismatch = AnalysisService(adapters={"sage": adapter}).analyze(
        sage_request(configuration=changed)
    )
    assert mismatch.result.failure.code == "configuration_mismatch"


def test_missing_executable_and_changed_runtime_configuration_are_structured():
    missing = AnalysisService(
        adapters={"sage": SageAdapter(FakeRuntime(FileNotFoundError("BGSage Python executable is unavailable")))}
    ).analyze(sage_request())
    assert missing.result.failure.code == "engine_failure"
    changed = AnalysisService(
        adapters={"sage": SageAdapter(FakeRuntime(ValueError("BGSage model identity changed")))}
    ).analyze(sage_request())
    assert changed.result.failure.code == "configuration_mismatch"


def test_changed_sage_version_is_configuration_mismatch():
    changed = identity()
    changed["engine_version"] = "changed"

    def runner(*args, **kwargs):
        return ProcessOutcome(
            "complete",
            0,
            canonical_json({"identity": changed, "protocol": SAGE_PROTOCOL_VERSION, "status": "complete"}) + "\n",
            "",
            None,
        )

    response = AnalysisService(adapters={"sage": SageAdapter(FakeRuntime(), runner)}).analyze(sage_request())
    assert response.result.failure.code == "configuration_mismatch"


def test_sage_timeout_nonzero_and_malformed_output_are_structured():
    identity_text = canonical_json(
        {"identity": identity(), "protocol": SAGE_PROTOCOL_VERSION, "status": "complete"}
    ) + "\n"

    def timeout_runner(command, **kwargs):
        if json.loads(kwargs["stdin_text"])["operation"] == "identify":
            return ProcessOutcome("complete", 0, identity_text, "", None)
        return ProcessOutcome("failed", None, "", "", "timeout")

    timed = AnalysisService(adapters={"sage": SageAdapter(FakeRuntime(), timeout_runner)}).analyze(sage_request())
    assert timed.result.failure.code == "timeout"

    def failure_runner(command, **kwargs):
        if json.loads(kwargs["stdin_text"])["operation"] == "identify":
            return ProcessOutcome("complete", 0, identity_text, "", None)
        return ProcessOutcome("failed", 9, "", "protocol failed", "engine_failure")

    failed = AnalysisService(adapters={"sage": SageAdapter(FakeRuntime(), failure_runner)}).analyze(sage_request())
    assert failed.result.failure.code == "engine_failure"
    assert "nonzero (9)" in failed.result.failure.message

    with pytest.raises(MalformedRawResponse, match="valid JSON"):
        SageJsonParser().parse(sage_request(), RawSource.from_output("not-json\n"))


def test_malformed_candidate_and_unrecognized_layout_are_rejected():
    payload = json.loads(raw_output())
    del payload["analysis"]["candidates"][0]["equity"]
    with pytest.raises(MalformedRawResponse, match="layout"):
        SageJsonParser().parse(
            sage_request(), RawSource.from_output(canonical_json(payload) + "\n")
        )
    payload = json.loads(raw_output("cube"))
    payload["analysis"]["actions"][1]["action"] = "Unknown"
    with pytest.raises(MalformedRawResponse, match="layout"):
        SageJsonParser().parse(
            sage_request("cube"), RawSource.from_output(canonical_json(payload) + "\n")
        )
