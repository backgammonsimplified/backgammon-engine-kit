"""Strict parser for the verified BGSage JSON protocol output."""

import json
import math
import re

from ..adapters import EngineOutputParser, MalformedRawResponse
from ..models import (
    AnalysisResult,
    CheckerCandidate,
    CheckerDecision,
    ConfigurationTrace,
    CubeAction,
    CubeDecision,
    OutcomeProbabilities,
)
from ..serialization import stable_hash
from .config import (
    SAGE_BEAROFF_SHA256,
    SAGE_ENGINE_VERSION,
    SAGE_MODEL_IDENTITY,
    SAGE_MODEL_NAME,
    SAGE_NATIVE_SHA256,
    SAGE_PROTOCOL_VERSION,
    sage_configuration_settings,
)


def _object(value, label):
    if not isinstance(value, dict):
        raise MalformedRawResponse("BGSage {} must be an object".format(label))
    return value


def _exact_keys(value, keys, label):
    if set(value) != set(keys):
        raise MalformedRawResponse("BGSage {} layout is unrecognized".format(label))


def _number(value, label):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise MalformedRawResponse("BGSage {} must be a finite number".format(label))
    return float(value)


def _probabilities(value):
    value = _object(value, "probabilities")
    _exact_keys(
        value,
        ("win", "gammon_win", "backgammon_win", "gammon_loss", "backgammon_loss"),
        "probability",
    )
    return OutcomeProbabilities(
        win=_number(value["win"], "win probability"),
        win_gammon=_number(value["gammon_win"], "gammon-win probability"),
        win_backgammon=_number(value["backgammon_win"], "backgammon-win probability"),
        lose=None,
        lose_gammon=_number(value["gammon_loss"], "gammon-loss probability"),
        lose_backgammon=_number(value["backgammon_loss"], "backgammon-loss probability"),
    )


def _ply(value):
    if not isinstance(value, str):
        raise MalformedRawResponse("BGSage evaluation depth is malformed")
    match = re.fullmatch(r"([1-4])-?ply", value.strip().lower())
    if match is None:
        raise MalformedRawResponse("BGSage output exposes an unsupported evaluation depth")
    return int(match.group(1))


def _expected_ply(request):
    try:
        return int(request.analysis_setting[:-3])
    except (TypeError, ValueError):
        raise MalformedRawResponse("BGSage request has an unsupported evaluation depth")


def _validate_identity(output):
    identity = _object(output.get("identity"), "identity")
    required = {
        "bearoff_sha256": SAGE_BEAROFF_SHA256,
        "engine_version": SAGE_ENGINE_VERSION,
        "model": SAGE_MODEL_NAME,
        "model_identity": SAGE_MODEL_IDENTITY,
        "native_module_sha256": SAGE_NATIVE_SHA256,
        "protocol": SAGE_PROTOCOL_VERSION,
    }
    if identity != required:
        raise MalformedRawResponse("BGSage output identity differs from verified evidence")


def _validate_request(request, output):
    identity = _object(output.get("request_identity"), "request identity")
    position_id, match_id = request.position.id.split(":", 1)
    expected = {
        "analysis_setting": request.analysis_setting,
        "decision_type": request.decision_type,
        "match_id": match_id,
        "position_id": position_id,
    }
    if identity != expected:
        raise MalformedRawResponse("BGSage output does not preserve request identity")
    context = _object(output.get("normalized_input"), "normalized input")
    required_context = (
        "beaver",
        "board",
        "crawford",
        "cube_owner",
        "cube_value",
        "dice",
        "jacoby",
        "match_length",
        "on_roll",
        "opponent_away",
        "opponent_score",
        "player_away",
        "player_score",
    )
    _exact_keys(context, required_context, "normalized input")
    if not isinstance(context["board"], list) or len(context["board"]) != 26 or not all(
        isinstance(point, int) and not isinstance(point, bool) for point in context["board"]
    ):
        raise MalformedRawResponse("BGSage normalized board is malformed")
    if context["dice"] != (list(request.dice) if request.dice is not None else None):
        raise MalformedRawResponse("BGSage output changed the requested dice")
    if context["on_roll"] not in ("X", "O"):
        raise MalformedRawResponse("BGSage player perspective is unrecognized")
    if context["cube_owner"] not in ("centered", "player", "opponent"):
        raise MalformedRawResponse("BGSage cube-owner perspective is unrecognized")


def _validate_configuration(output, request):
    configuration = _object(output.get("configuration"), "configuration")
    settings = sage_configuration_settings(request.configuration)
    if settings["legacy"] and request.analysis_setting == "1ply":
        expected = {
            "candidate_generation": "all-legal-moves" if request.decision_type == "checker" else "not-applicable",
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
    else:
        expected = {
            "analysis_setting": request.analysis_setting,
            "candidate_generation": "all-legal-moves" if request.decision_type == "checker" else "not-applicable",
            "cubeful": True,
            "include_game_plans": False,
            "include_two_ply_cube_details": False,
            "model": "stage9",
            "parallel_threads": settings["parallel_threads"],
            "seed": settings["seed"],
        }
    if configuration != expected:
        raise MalformedRawResponse("BGSage output configuration differs from the pinned profile")


class SageJsonParser(EngineOutputParser):
    """Parse checker and cube JSON layouts and verify actual evaluation depth."""

    def parse(self, request, raw_source, started_at=None, completed_at=None):
        if raw_source.inline is None:
            raise MalformedRawResponse("BGSage parser requires immutable inline output")
        try:
            output = json.loads(raw_source.inline)
        except (TypeError, ValueError):
            raise MalformedRawResponse("BGSage output is not valid JSON")
        output = _object(output, "output")
        _exact_keys(
            output,
            (
                "analysis",
                "configuration",
                "identity",
                "normalized_input",
                "protocol",
                "request_identity",
                "status",
            ),
            "output",
        )
        if output["protocol"] != SAGE_PROTOCOL_VERSION or output["status"] != "complete":
            raise MalformedRawResponse("BGSage protocol did not return a complete result")
        _validate_identity(output)
        _validate_request(request, output)
        _validate_configuration(output, request)
        analysis = _object(output["analysis"], "analysis")
        if analysis.get("type") != request.decision_type:
            raise MalformedRawResponse("BGSage decision type changed during analysis")
        expected_ply = _expected_ply(request)
        if request.decision_type == "checker":
            checker, warnings = self._parse_checker(analysis, expected_ply)
            cube = None
        else:
            checker = None
            cube, warnings = self._parse_cube(analysis, expected_ply)
        return AnalysisResult(
            position=request.position,
            engine=request.engine,
            analysis_setting=request.analysis_setting,
            configuration_trace=ConfigurationTrace.from_configuration(request.configuration),
            decision_type=request.decision_type,
            status="complete",
            checker_decision=checker,
            cube_decision=cube,
            warnings=warnings,
            assumptions=(),
            raw_source=raw_source,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _parse_checker(self, analysis, expected_ply):
        _exact_keys(
            analysis,
            ("board", "candidate_count", "candidates", "dice", "eval_level", "type"),
            "checker analysis",
        )
        actual_ply = _ply(analysis["eval_level"])
        if actual_ply != expected_ply:
            raise MalformedRawResponse("BGSage checker actual depth differs from the requested profile")
        candidates_raw = analysis["candidates"]
        if not isinstance(candidates_raw, list) or not candidates_raw:
            raise MalformedRawResponse("BGSage checker output contains no candidates")
        if analysis["candidate_count"] != len(candidates_raw):
            raise MalformedRawResponse("BGSage checker candidate count changed")
        candidates = []
        for expected_rank, item in enumerate(candidates_raw, 1):
            item = _object(item, "checker candidate")
            _exact_keys(
                item,
                (
                    "board",
                    "cubeless_equity",
                    "equity",
                    "equity_difference",
                    "eval_level",
                    "move_notation",
                    "notation_source",
                    "probabilities",
                    "rank",
                ),
                "checker candidate",
            )
            board = item["board"]
            if not isinstance(board, list) or len(board) != 26 or not all(
                isinstance(point, int) and not isinstance(point, bool) for point in board
            ):
                raise MalformedRawResponse("BGSage checker candidate board is malformed")
            if item["rank"] != expected_rank:
                raise MalformedRawResponse("BGSage checker candidate ranks are not contiguous")
            if not isinstance(item["move_notation"], str) or not item["move_notation"]:
                raise MalformedRawResponse("BGSage checker candidate notation is malformed")
            if item["notation_source"] != "bgsage.possible_single_die_moves-v1":
                raise MalformedRawResponse("BGSage checker notation source is unrecognized")
            candidate_ply = _ply(item["eval_level"])
            if candidate_ply != expected_ply:
                raise MalformedRawResponse("BGSage checker candidate depth differs from the requested profile")
            candidates.append(
                CheckerCandidate(
                    move_id="sage-move-{}-{}".format(expected_rank, stable_hash(board)[:12]),
                    rank=expected_rank,
                    notation=item["move_notation"],
                    raw_notation=None,
                    notation_source=item["notation_source"],
                    is_played_move=None,
                    equity=_number(item["equity"], "checker equity"),
                    equity_difference=_number(item["equity_difference"], "checker equity difference"),
                    probabilities=_probabilities(item["probabilities"]),
                    actual_ply=candidate_ply,
                    resulting_position_id=None,
                    cubeful=True,
                )
            )
        return (
            CheckerDecision(
                candidates=tuple(candidates),
                recommended_move_id=candidates[0].move_id,
                actual_evaluation_type="neural-network-evaluation",
                actual_ply=actual_ply,
                cubeful=True,
                requested_candidate_count=None,
                exported_candidate_count=len(candidates),
                move_filter=None,
            ),
            (
                "BGSage emits no textual move notation; normalized notation is reconstructed by its legal single-die move generator and raw_notation remains null",
                "BGSage emits five probability fields; the unavailable aggregate lose probability remains null",
                "BGSage did not identify a played move or resulting position identifier; those fields remain null",
                "cubeless candidate equity and emitted candidate boards remain available in the immutable raw source",
            ),
        )

    def _parse_cube(self, analysis, expected_ply):
        _exact_keys(
            analysis,
            (
                "actions",
                "cubeless_equity",
                "details",
                "eval_level",
                "is_beaver",
                "optimal_action",
                "optimal_equity",
                "probabilities",
                "should_double",
                "should_take",
                "type",
            ),
            "cube analysis",
        )
        actual_ply = _ply(analysis["eval_level"])
        if actual_ply != expected_ply:
            raise MalformedRawResponse("BGSage cube actual depth differs from the requested profile")
        if expected_ply == 1 and analysis["details"] is not None:
            raise MalformedRawResponse("BGSage 1-ply cube output unexpectedly contains deeper details")
        actions_raw = analysis["actions"]
        if not isinstance(actions_raw, list) or len(actions_raw) != 3:
            raise MalformedRawResponse("BGSage cube output lacks the verified three-action layout")
        labels = [item.get("action") if isinstance(item, dict) else None for item in actions_raw]
        expected_second = "Double/Beaver" if analysis["is_beaver"] is True else "Double/Take"
        if labels != ["No Double", expected_second, "Double/Pass"]:
            raise MalformedRawResponse("BGSage cube action layout is unrecognized")
        actions = []
        action_ids = {
            "No Double": "no-double",
            "Double/Take": "double-take",
            "Double/Pass": "double-pass",
            "Double/Beaver": "double-beaver",
        }
        for expected_order, item in enumerate(actions_raw, 1):
            _exact_keys(item, ("action", "equity", "output_order"), "cube action")
            if item["output_order"] != expected_order:
                raise MalformedRawResponse("BGSage cube action output order changed")
            actions.append(
                CubeAction(
                    action_id=action_ids[item["action"]],
                    rank=expected_order,
                    label=item["action"],
                    equity=_number(item["equity"], "cube action equity"),
                    match_winning_chance=None,
                    probabilities=None,
                )
            )
        raw_recommendation = analysis["optimal_action"]
        if raw_recommendation not in action_ids:
            raise MalformedRawResponse("BGSage cube recommendation is unrecognized")
        recommended = action_ids[raw_recommendation]
        if recommended not in {action.action_id for action in actions}:
            raise MalformedRawResponse("BGSage cube recommendation does not identify an emitted action")
        _number(analysis["optimal_equity"], "optimal cube equity")
        if not isinstance(analysis["should_double"], bool) or not isinstance(analysis["should_take"], bool):
            raise MalformedRawResponse("BGSage cube booleans are malformed")
        return (
            CubeDecision(
                actions=tuple(actions),
                recommended_action_id=recommended,
                gnu_recommendation=None,
                raw_recommendation=raw_recommendation,
                actual_evaluation_type="neural-network-evaluation",
                actual_ply=actual_ply,
                cubeful=True,
                cubeless_equity=_number(analysis["cubeless_equity"], "cubeless equity"),
                probabilities=_probabilities(analysis["probabilities"]),
                cube_efficiency=None,
            ),
            (
                "cube action ranks preserve protocol field order, not action desirability",
                "BGSage emits five probability fields; the unavailable aggregate lose probability remains null",
                "match-winning chances, action probabilities, and cube efficiency were not emitted and remain null",
            ),
        )
