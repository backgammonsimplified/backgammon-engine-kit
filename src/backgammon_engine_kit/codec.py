"""Strict JSON-to-model decoding for the public CLI and cache."""

from .models import (
    AnalysisRequest,
    AnalysisResult,
    CheckerCandidate,
    CheckerDecision,
    ConfigurationTrace,
    CubeAction,
    CubeDecision,
    EngineConfiguration,
    EngineFailure,
    MoveFilter,
    NormalizedPosition,
    OutcomeProbabilities,
    Position,
    RawSource,
)


def _required(data, key):
    if key not in data:
        raise ValueError("missing required field: {}".format(key))
    return data[key]


def normalized_position_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("normalized position must be an object")
    return NormalizedPosition(
        board=tuple(_required(data, "board")),
        on_roll=_required(data, "on_roll"),
        dice=tuple(data["dice"]) if data.get("dice") is not None else None,
        cube_value=_required(data, "cube_value"),
        cube_owner=_required(data, "cube_owner"),
        match_length=_required(data, "match_length"),
        player_score=_required(data, "player_score"),
        opponent_score=_required(data, "opponent_score"),
        crawford=_required(data, "crawford"),
        jacoby=_required(data, "jacoby"),
        beaver=_required(data, "beaver"),
    )


def position_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("position must be an object")
    normalized = data.get("normalized")
    return Position(
        id=data.get("id"),
        format=_required(data, "format"),
        normalized=normalized_position_from_dict(normalized) if normalized is not None else None,
    )


def configuration_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    options = data.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("configuration options must be an object")
    return EngineConfiguration(
        engine=_required(data, "engine"),
        profile=_required(data, "profile"),
        engine_version=data.get("engine_version"),
        model_or_weights_identity=data.get("model_or_weights_identity"),
        invocation_identity=data.get("invocation_identity"),
        parser_version=data.get("parser_version"),
        options=tuple(options.items()),
    )


def request_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("request must be an object")
    dice = data.get("dice")
    return AnalysisRequest(
        position=position_from_dict(_required(data, "position")),
        engine=_required(data, "engine"),
        analysis_setting=_required(data, "analysis_setting"),
        decision_type=_required(data, "decision_type"),
        dice=tuple(dice) if dice is not None else None,
        report_mode=data.get("report_mode", "quick"),
        report_mode_changes_data=data.get("report_mode_changes_data", False),
        configuration=configuration_from_dict(_required(data, "configuration")),
    )


def probabilities_from_dict(data):
    if data is None:
        return None
    return OutcomeProbabilities(
        win=data.get("win"),
        win_gammon=data.get("win_gammon"),
        win_backgammon=data.get("win_backgammon"),
        lose=data.get("lose"),
        lose_gammon=data.get("lose_gammon"),
        lose_backgammon=data.get("lose_backgammon"),
    )


def checker_decision_from_dict(data):
    if data is None:
        return None
    candidates = []
    for candidate in _required(data, "candidates"):
        candidates.append(
            CheckerCandidate(
                move_id=_required(candidate, "move_id"),
                rank=_required(candidate, "rank"),
                notation=candidate.get("notation"),
                raw_notation=candidate.get("raw_notation"),
                is_played_move=candidate.get("is_played_move"),
                equity=candidate.get("equity"),
                equity_difference=candidate.get("equity_difference"),
                probabilities=probabilities_from_dict(candidate.get("probabilities")),
                actual_ply=candidate.get("actual_ply"),
                resulting_position_id=candidate.get("resulting_position_id"),
                cubeful=candidate.get("cubeful"),
                notation_source=candidate.get("notation_source"),
            )
        )
    move_filter = data.get("move_filter")
    return CheckerDecision(
        tuple(candidates),
        data.get("recommended_move_id"),
        actual_evaluation_type=data.get("actual_evaluation_type"),
        actual_ply=data.get("actual_ply"),
        cubeful=data.get("cubeful"),
        requested_candidate_count=data.get("requested_candidate_count"),
        exported_candidate_count=data.get("exported_candidate_count"),
        move_filter=(
            MoveFilter(
                evaluation_ply=_required(move_filter, "evaluation_ply"),
                accept_ply=_required(move_filter, "accept_ply"),
                extra_moves=_required(move_filter, "extra_moves"),
                tolerance=_required(move_filter, "tolerance"),
            )
            if move_filter is not None
            else None
        ),
    )


def cube_decision_from_dict(data):
    if data is None:
        return None
    actions = []
    for action in _required(data, "actions"):
        actions.append(
            CubeAction(
                action_id=_required(action, "action_id"),
                rank=_required(action, "rank"),
                label=_required(action, "label"),
                equity=action.get("equity"),
                match_winning_chance=action.get("match_winning_chance"),
                probabilities=probabilities_from_dict(action.get("probabilities")),
            )
        )
    return CubeDecision(
        tuple(actions),
        data.get("recommended_action_id"),
        gnu_recommendation=data.get("gnu_recommendation"),
        actual_evaluation_type=data.get("actual_evaluation_type"),
        actual_ply=data.get("actual_ply"),
        cubeful=data.get("cubeful"),
        cubeless_equity=data.get("cubeless_equity"),
        probabilities=probabilities_from_dict(data.get("probabilities")),
        cube_efficiency=data.get("cube_efficiency"),
        raw_recommendation=data.get("raw_recommendation"),
    )


def raw_source_from_dict(data):
    if data is None:
        return None
    return RawSource(
        inline=data.get("inline"),
        content_sha256=_required(data, "content_sha256"),
        reference=data.get("reference"),
        captured_at=data.get("captured_at"),
    )


def result_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("result must be an object")
    engine = _required(data, "engine")
    trace = _required(data, "configuration_trace")
    failure = data.get("failure")
    return AnalysisResult(
        position=position_from_dict(_required(data, "position")),
        engine=_required(engine, "name"),
        analysis_setting=_required(engine, "analysis_setting"),
        configuration_trace=ConfigurationTrace(
            configuration_hash=_required(trace, "configuration_hash"),
            engine_version=trace.get("engine_version"),
            model_or_weights_identity=trace.get("model_or_weights_identity"),
            invocation_identity=trace.get("invocation_identity"),
            parser_version=trace.get("parser_version"),
        ),
        decision_type=_required(data, "decision_type"),
        status=_required(data, "status"),
        checker_decision=checker_decision_from_dict(data.get("checker_decision")),
        cube_decision=cube_decision_from_dict(data.get("cube_decision")),
        warnings=tuple(data.get("warnings", ())),
        assumptions=tuple(data.get("assumptions", ())),
        raw_source=raw_source_from_dict(data.get("raw_source")),
        failure=(
            EngineFailure(
                code=_required(failure, "code"),
                message=_required(failure, "message"),
                retryable=_required(failure, "retryable"),
            )
            if failure is not None
            else None
        ),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
    )
