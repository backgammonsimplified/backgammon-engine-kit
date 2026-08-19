"""Parser for verified GNU 1.08.003 `hint` transcript forms."""

import re

from ..adapters import EngineOutputParser, MalformedRawResponse
from ..models import (
    AnalysisResult,
    CheckerCandidate,
    CheckerDecision,
    ConfigurationTrace,
    CubeAction,
    CubeDecision,
    MoveFilter,
    OutcomeProbabilities,
)
from .config import gnu_configuration_settings


_CHECKER_HEADER = re.compile(
    r"^\s*(?:\*)?\s*(\d+)\.\s+(Cubeful|Cubeless)\s+(\d+)-ply\s+(.+?)\s+Eq\.:\s+"
    r"([+-]?\d+\.\d+)(?:\s+\(([+-]?\d+\.\d+)\))?\s*$"
)
_PROBABILITIES = re.compile(
    r"^\s*(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+-\s+"
    r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$"
)
_CUBELESS = re.compile(r"^\s*(\d+)-ply cubeless equity\s+([+-]?\d+\.\d+)(?:\s+.*)?$")
_CUBE_ACTION = re.compile(
    r"^\s*(\d+)\.\s+(No double|Double, pass|Double, take)\s+"
    r"([+-]?\d+\.\d+)(?:\s+\(([+-]?\d+\.\d+)\))?\s*$"
)
_RECOMMENDATION = re.compile(r"^\s*Proper cube action:\s+(.+?)\s*$")
_CUBE_EFFICIENCY = re.compile(r"^\s*Cube efficiency:\s+([+-]?\d+\.\d+)\s*$")


def _probabilities(match):
    values = [float(match.group(index)) for index in range(1, 7)]
    return OutcomeProbabilities(
        win=values[0],
        win_gammon=values[1],
        win_backgammon=values[2],
        lose=values[3],
        lose_gammon=values[4],
        lose_backgammon=values[5],
    )


def _expected_ply(request):
    try:
        return int(request.analysis_setting[:-3])
    except (TypeError, ValueError):
        raise MalformedRawResponse("GNU request has an unsupported evaluation depth")


def _require_identity(request, output):
    position_id, match_id = request.position.id.split(":", 1)
    if "Position ID: {}".format(position_id) not in output:
        raise MalformedRawResponse("GNU output did not preserve the requested Position ID")
    if "Match ID   : {}".format(match_id) not in output:
        raise MalformedRawResponse("GNU output did not preserve the requested Match ID")
    if request.decision_type == "checker":
        dice_text = "has rolled {} and {}.".format(request.dice[0], request.dice[1])
        if dice_text not in output:
            raise MalformedRawResponse("GNU output did not preserve the requested checker dice")
    elif "has not yet rolled the dice." not in output:
        raise MalformedRawResponse("GNU cube output unexpectedly contains checker dice")


def _evaluation_is_verified(output, expected_ply, threads):
    rejected = ("Unknown keyword", "You must set", "Error:")
    if any(marker in output for marker in rejected):
        return False
    thread_marker = "{} calculation thread{}.".format(threads, "" if threads == 1 else "s")
    required = (
        "will use {} ply evaluation.".format(expected_ply),
        "will use cubeful evaluation.",
        "will use deterministic noise.",
        "will use noiseless evaluations.",
        "will not use pruning.",
        "keep the first 0 0-ply moves and up to 8 more moves within equity 0.16",
        thread_marker,
        "Game winning chances will be shown as probabilities.",
        "Match evaluations will be shown as equivalent money equity.",
    )
    return all(marker in output for marker in required)


class GnuTextParser(EngineOutputParser):
    """Strictly parse evidenced checker/cube layouts and verify actual depth."""

    def parse(self, request, raw_source, started_at=None, completed_at=None):
        output = raw_source.inline
        if output is None:
            raise MalformedRawResponse("GNU parser requires immutable inline output")
        _require_identity(request, output)
        settings = gnu_configuration_settings(request.configuration)
        expected_ply = _expected_ply(request)
        if not _evaluation_is_verified(output, expected_ply, settings["threads"]):
            raise MalformedRawResponse("GNU output does not verify the pinned evaluation profile")
        if request.decision_type == "checker":
            decision, warnings = self._parse_checker(output, expected_ply)
            checker_decision, cube_decision = decision, None
        else:
            decision, warnings = self._parse_cube(output, expected_ply)
            checker_decision, cube_decision = None, decision
        return AnalysisResult(
            position=request.position,
            engine=request.engine,
            analysis_setting=request.analysis_setting,
            configuration_trace=ConfigurationTrace.from_configuration(request.configuration),
            decision_type=request.decision_type,
            status="complete",
            checker_decision=checker_decision,
            cube_decision=cube_decision,
            warnings=warnings,
            assumptions=(),
            raw_source=raw_source,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _parse_checker(self, output, expected_ply):
        lines = output.splitlines()
        candidates = []
        for index, line in enumerate(lines):
            header = _CHECKER_HEADER.match(line)
            if header is None:
                continue
            if index + 1 >= len(lines):
                raise MalformedRawResponse("GNU checker candidate lacks probabilities")
            chance = _PROBABILITIES.match(lines[index + 1])
            if chance is None:
                raise MalformedRawResponse("GNU checker probabilities are malformed")
            rank = int(header.group(1))
            raw_notation = header.group(4).strip()
            candidate_ply = int(header.group(3))
            if candidate_ply != expected_ply:
                raise MalformedRawResponse("GNU checker actual depth differs from the requested profile")
            candidates.append(
                CheckerCandidate(
                    move_id="gnu-move-{}".format(rank),
                    rank=rank,
                    notation=" ".join(raw_notation.split()),
                    raw_notation=raw_notation,
                    is_played_move=None,
                    equity=float(header.group(5)),
                    equity_difference=(float(header.group(6)) if header.group(6) is not None else None),
                    probabilities=_probabilities(chance),
                    actual_ply=candidate_ply,
                    resulting_position_id=None,
                    cubeful=header.group(2) == "Cubeful",
                )
            )
        if not candidates:
            raise MalformedRawResponse("GNU checker output contains no verified candidates")
        candidates.sort(key=lambda candidate: candidate.rank)
        if [candidate.rank for candidate in candidates] != list(range(1, len(candidates) + 1)):
            raise MalformedRawResponse("GNU checker candidate ranks are not contiguous")
        actual_ply = candidates[0].actual_ply
        cubeful = candidates[0].cubeful
        return (
            CheckerDecision(
                candidates=tuple(candidates),
                recommended_move_id=candidates[0].move_id,
                actual_evaluation_type="evaluation",
                actual_ply=actual_ply,
                cubeful=cubeful,
                requested_candidate_count=8,
                exported_candidate_count=len(candidates),
                move_filter=MoveFilter(1, 0, 8, 0.160),
            ),
            (
                "GNU hint output does not identify a previously played move; is_played_move remains null",
                "resulting Position IDs were not generated; resulting_position_id remains null",
            ),
        )

    def _parse_cube(self, output, expected_ply):
        lines = output.splitlines()
        cubeless_equity = None
        probabilities = None
        actual_ply = None
        actions = []
        recommendation = None
        cube_efficiency = None
        for index, line in enumerate(lines):
            cubeless = _CUBELESS.match(line)
            if cubeless is not None:
                actual_ply = int(cubeless.group(1))
                if actual_ply != expected_ply:
                    raise MalformedRawResponse("GNU cube actual depth differs from the requested profile")
                cubeless_equity = float(cubeless.group(2))
                if index + 1 >= len(lines):
                    raise MalformedRawResponse("GNU cube output lacks probabilities")
                chance = _PROBABILITIES.match(lines[index + 1])
                if chance is None:
                    raise MalformedRawResponse("GNU cube probabilities are malformed")
                probabilities = _probabilities(chance)
                continue
            action = _CUBE_ACTION.match(line)
            if action is not None:
                label = action.group(2)
                action_id = label.lower().replace(",", "").replace(" ", "-")
                actions.append(
                    CubeAction(
                        action_id=action_id,
                        rank=int(action.group(1)),
                        label=label,
                        equity=float(action.group(3)),
                        match_winning_chance=None,
                        probabilities=None,
                    )
                )
                continue
            proper = _RECOMMENDATION.match(line)
            if proper is not None:
                recommendation = proper.group(1)
                continue
            efficiency = _CUBE_EFFICIENCY.match(line)
            if efficiency is not None:
                cube_efficiency = float(efficiency.group(1))
        if actual_ply is None or probabilities is None or cubeless_equity is None:
            raise MalformedRawResponse("GNU cube output lacks verified cubeless evaluation data")
        if len(actions) != 3 or recommendation is None:
            raise MalformedRawResponse("GNU cube output lacks the verified three-action layout")
        actions.sort(key=lambda action: action.rank)
        if recommendation.startswith("No double"):
            recommended_action_id = "no-double"
        elif recommendation.startswith("Double, pass"):
            recommended_action_id = "double-pass"
        elif recommendation.startswith("Double, take"):
            recommended_action_id = "double-take"
        else:
            raise MalformedRawResponse("GNU cube recommendation form is unsupported")
        if recommended_action_id not in {action.action_id for action in actions}:
            raise MalformedRawResponse("GNU cube recommendation does not identify an emitted action")
        return (
            CubeDecision(
                actions=tuple(actions),
                recommended_action_id=recommended_action_id,
                gnu_recommendation=recommendation,
                raw_recommendation=recommendation,
                actual_evaluation_type="evaluation",
                actual_ply=actual_ply,
                cubeful=True,
                cubeless_equity=cubeless_equity,
                probabilities=probabilities,
                cube_efficiency=cube_efficiency,
            ),
            (
                "GNU cube-action equities are preserved as emitted; action probabilities and match-winning chances remain null",
            ),
        )
