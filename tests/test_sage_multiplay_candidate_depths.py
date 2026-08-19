import pytest

from backgammon_engine_kit.adapters import MalformedRawResponse
from backgammon_engine_kit.sage.parser import SageJsonParser


def probabilities():
    return {
        "backgammon_loss": 0.01,
        "backgammon_win": 0.02,
        "gammon_loss": 0.10,
        "gammon_win": 0.20,
        "win": 0.60,
    }


def candidate(rank, eval_level):
    return {
        "board": [rank] + [0] * 25,
        "cubeless_equity": 0.30 - rank / 100.0,
        "equity": 0.40 - rank / 100.0,
        "equity_difference": 0.0 if rank == 1 else -rank / 100.0,
        "eval_level": eval_level,
        "move_notation": "8/{}".format(max(1, 5 - rank)),
        "notation_source": "bgsage.possible_single_die_moves-v1",
        "probabilities": probabilities(),
        "rank": rank,
    }


def analysis(levels, result_level="4-ply"):
    candidates = [candidate(rank, level) for rank, level in enumerate(levels, 1)]
    return {
        "board": [0] * 26,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "dice": [4, 2],
        "eval_level": result_level,
        "type": "checker",
    }


def test_multiplay_checker_preserves_filtered_one_ply_candidates():
    decision, warnings = SageJsonParser()._parse_checker(
        analysis(("4-ply", "4-ply", "1-ply", "1-ply")),
        4,
    )
    assert decision.actual_ply == 4
    assert [item.actual_ply for item in decision.candidates] == [4, 4, 1, 1]
    assert any("filtering" in warning for warning in warnings)


def test_multiplay_checker_requires_recommended_move_at_requested_depth():
    with pytest.raises(MalformedRawResponse, match="recommended move depth"):
        SageJsonParser()._parse_checker(
            analysis(("1-ply", "4-ply", "1-ply")),
            4,
        )


def test_multiplay_checker_requires_promoted_second_best_at_requested_depth():
    with pytest.raises(MalformedRawResponse, match="second-best move"):
        SageJsonParser()._parse_checker(
            analysis(("4-ply", "1-ply", "1-ply")),
            4,
        )


def test_multiplay_checker_rejects_intermediate_candidate_depth():
    with pytest.raises(MalformedRawResponse, match="inconsistent with filtering"):
        SageJsonParser()._parse_checker(
            analysis(("4-ply", "4-ply", "3-ply")),
            4,
        )


def test_checker_top_level_depth_still_must_match_request():
    with pytest.raises(MalformedRawResponse, match="actual depth"):
        SageJsonParser()._parse_checker(
            analysis(("4-ply", "4-ply"), result_level="3-ply"),
            4,
        )
