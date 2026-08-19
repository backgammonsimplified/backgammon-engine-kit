from backgammon_engine_kit.adapters import MalformedRawResponse
from backgammon_engine_kit.gnu.config import gnu_configuration
from backgammon_engine_kit.gnu.parser import GnuTextParser
from backgammon_engine_kit.models import AnalysisRequest, Position, RawSource

import pytest


POSITION = "4PPgASTgc/ABMA:cAnqAAAAAAAE"


def request():
    return AnalysisRequest(
        position=Position(id=POSITION, format="gnuid"),
        engine="gnu",
        analysis_setting="3ply",
        decision_type="checker",
        dice=(4, 2),
        configuration=gnu_configuration(checker_plies=3, cube_plies=2),
    )


def output(candidate_ply=0):
    return "\n".join(
        (
            "GNU Backgammon 1.08.003 20260710",
            " GNU Backgammon  Position ID: 4PPgASTgc/ABMA",
            "                 Match ID   : cAnqAAAAAAAE",
            "player1 has rolled 4 and 2.",
            "`eval' and `hint' chequerplay will use 3 ply evaluation.",
            "`eval' and `hint' chequerplay will use cubeful evaluation.",
            "`eval' and `hint' chequerplay will use deterministic noise.",
            "`eval' and `hint' chequerplay will use noiseless evaluations.",
            "`eval' and `hint' chequerplay will not use pruning.",
            "`eval' and `hint' cube decisions will use 2 ply evaluation.",
            "`eval' and `hint' cube decisions will use cubeful evaluation.",
            "`eval' and `hint' cube decisions will use deterministic noise.",
            "`eval' and `hint' cube decisions will use noiseless evaluations.",
            "`eval' and `hint' cube decisions will not use pruning.",
            "Move filter for 1 ply:",
            "  keep the first 0 0-ply moves and up to 8 more moves within equity 0.16",
            "Move filter for 2 ply:",
            "  keep the first 0 0-ply moves and up to 8 more moves within equity 0.16",
            "  Skip pruning for 1-ply moves.",
            "Move filter for 3 ply:",
            "  keep the first 0 0-ply moves and up to 8 more moves within equity 0.16",
            "  Skip pruning for 1-ply moves.",
            "  keep the first 0 2-ply moves and up to 2 more moves within equity 0.04",
            "Move filter for 4 ply:",
            "  keep the first 0 0-ply moves and up to 8 more moves within equity 0.16",
            "  Skip pruning for 1-ply moves.",
            "  keep the first 0 2-ply moves and up to 2 more moves within equity 0.04",
            "  Skip pruning for 3-ply moves.",
            "1 calculation thread.",
            "Game winning chances will be shown as probabilities.",
            "Match evaluations will be shown as equivalent money equity.",
            "    1. Cubeful {}-ply    8/4 6/4                      Eq.: +0.025297".format(candidate_ply),
            "       0.505167 0.140466 0.004389 - 0.494833 0.131013 0.006270",
            "",
        )
    )


def test_configured_three_ply_can_legitimately_return_shallower_recommendation():
    parsed = GnuTextParser().parse(request(), RawSource.from_output(output(candidate_ply=0)))
    assert parsed.analysis_setting == "3ply"
    assert parsed.checker_decision.actual_ply == 0
    assert parsed.checker_decision.candidates[0].actual_ply == 0
    assert any("Normal move filters" in warning for warning in parsed.warnings)


def test_candidate_depth_may_not_exceed_configured_checker_depth():
    with pytest.raises(MalformedRawResponse, match="exceeds the configured profile"):
        GnuTextParser().parse(request(), RawSource.from_output(output(candidate_ply=4)))


def test_move_filter_drift_is_rejected_even_when_plies_match():
    drifted = output(candidate_ply=0).replace(
        "up to 2 more moves within equity 0.04",
        "up to 2 more moves within equity 0.05",
    )
    with pytest.raises(MalformedRawResponse, match="pinned evaluation profile"):
        GnuTextParser().parse(request(), RawSource.from_output(drifted))
