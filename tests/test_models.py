from dataclasses import FrozenInstanceError

import pytest

from backgammon_engine_kit.codec import request_from_dict
from backgammon_engine_kit.models import (
    AnalysisRequest,
    AnalysisResult,
    CheckerDecision,
    ConfigurationTrace,
    EngineConfiguration,
    NormalizedPosition,
    Position,
    RawSource,
)
from backgammon_engine_kit.serialization import canonical_json

from helpers import VALID_XGID, checker_result, configuration, cube_result, request


def test_valid_checker_request_model_is_immutable():
    value = request(decision_type="checker")
    assert value.dice == (2, 1)
    with pytest.raises(FrozenInstanceError):
        value.engine = "gnu"


def test_valid_cube_request_model():
    value = request(decision_type="cube")
    assert value.decision_type == "cube"
    assert value.dice is None


def test_valid_gnu_request_model():
    value = request(engine="gnu", setting="4ply", decision_type="cube")
    assert value.engine == "gnu"
    assert value.configuration.engine == "gnu"


def test_complete_normalized_position_request():
    normalized = NormalizedPosition(
        board=(0,) * 26,
        on_roll="X",
        dice=(3, 1),
        cube_value=1,
        cube_owner="centered",
        match_length=0,
        player_score=0,
        opponent_score=0,
        crawford=False,
        jacoby=True,
        beaver=True,
    )
    value = AnalysisRequest(
        position=Position(id=None, format="normalized-position-v1", normalized=normalized),
        engine="sage",
        analysis_setting="2ply",
        decision_type="checker",
        dice=(3, 1),
        configuration=configuration(),
    )
    assert value.position.to_dict()["id"] is None


def test_sage_rollout_is_ordinary_analysis_setting():
    value = request(engine="sage", setting="rollout")
    data = value.to_dict()
    assert data["engine"] == "sage"
    assert data["analysis_setting"] == "rollout"
    assert "rollout" not in data


def test_deterministic_serialization():
    first = request().to_dict()
    second = request().to_dict()
    assert canonical_json(first) == canonical_json(second)
    assert canonical_json(first) == canonical_json(first)


def test_exactly_one_applicable_decision_section():
    valid = checker_result()
    assert valid.checker_decision is not None
    assert valid.cube_decision is None
    with pytest.raises(ValueError, match="exactly one"):
        AnalysisResult(
            position=valid.position,
            engine=valid.engine,
            analysis_setting=valid.analysis_setting,
            configuration_trace=valid.configuration_trace,
            decision_type="checker",
            status="complete",
            checker_decision=valid.checker_decision,
            cube_decision=cube_result().cube_decision,
            warnings=(),
            assumptions=(),
            raw_source=valid.raw_source,
        )


def test_cube_success_has_only_cube_section():
    valid = cube_result()
    data = valid.to_dict()
    assert data["checker_decision"] is None
    assert data["cube_decision"] is not None
    assert data["cube_decision"]["actions"][0]["equity"] is None


def test_unavailable_fields_remain_explicit_null():
    data = checker_result().to_dict()
    candidate = data["checker_decision"]["candidates"][0]
    assert candidate["equity"] is None
    assert candidate["probabilities"] is None
    assert candidate["is_played_move"] is None
    assert candidate["actual_ply"] is None
    assert candidate["resulting_position_id"] is None
    assert data["cube_decision"] is None
    assert data["engine"]["version"] is None


def test_invalid_engine():
    with pytest.raises(ValueError, match="invalid engine"):
        EngineConfiguration(engine="other", profile="test")


def test_invalid_analysis_setting():
    with pytest.raises(ValueError, match="invalid analysis setting"):
        AnalysisRequest(
            position=Position(id=VALID_XGID, format="xgid"),
            engine="sage",
            analysis_setting="world-class",
            decision_type="checker",
            dice=(2, 1),
            configuration=configuration(),
        )


def test_invalid_position_identifier():
    with pytest.raises(ValueError, match="invalid xgid"):
        Position(id="not-an-xgid", format="xgid")


def test_missing_decision_context():
    data = request().to_dict()
    data.pop("decision_type")
    with pytest.raises(ValueError, match="decision_type"):
        request_from_dict(data)


def test_checker_context_requires_dice():
    with pytest.raises(ValueError, match="requires two dice"):
        AnalysisRequest(
            position=Position(id=VALID_XGID, format="xgid"),
            engine="sage",
            analysis_setting="1ply",
            decision_type="checker",
            dice=None,
            configuration=configuration(),
        )


def test_configuration_validation_preserves_unknown_identity_as_null():
    value = configuration()
    data = value.to_dict()
    assert data["engine_version"] is None
    assert data["model_or_weights_identity"] is None
    assert len(data["configuration_hash"]) == 64


def test_raw_traceability_accepts_inline_or_content_address():
    inline = RawSource.from_output("immutable raw")
    reference = RawSource.from_hash(inline.content_sha256)
    assert inline.reference is None
    assert reference.reference == "sha256:" + inline.content_sha256
