"""One-request BGSage JSON protocol.

This file intentionally uses only the standard library plus the installed
BGSage and ankigammon distributions so it can be executed directly by the
verified BGSage Python interpreter.
"""

import base64
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys


PROTOCOL = "bgsage-position-analysis-v1"
MODEL = "stage9"
SUPPORTED_CHECKER_SETTINGS = frozenset(("1ply", "4ply"))
SUPPORTED_CUBE_SETTINGS = frozenset(("1ply", "3ply"))


def _canonical(data):
    return json.dumps(data, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _aggregate(models_dir):
    digest = hashlib.sha256()
    for path in sorted(path for path in Path(models_dir).iterdir() if path.is_file()):
        digest.update(("{}  {}\n".format(_sha256(path), path.name)).encode("utf-8"))
    return digest.hexdigest()


def _runtime_identity():
    import bgsage
    import bgbot_cpp

    package_dir = Path(bgsage.__file__).resolve().parent
    models_dir = package_dir / "_assets" / "models"
    bearoff = package_dir / "_assets" / "data" / "bearoff_1sided.db"
    model_digest = _aggregate(models_dir)
    return {
        "bearoff_sha256": _sha256(bearoff),
        "engine_version": importlib.metadata.version("bgsage"),
        "model": bgsage.PRODUCTION_MODEL,
        "model_identity": "{}:sha256:{}".format(bgsage.PRODUCTION_MODEL, model_digest),
        "native_module_sha256": _sha256(Path(bgbot_cpp.__file__)),
        "protocol": PROTOCOL,
    }, bearoff


def _gnubg_native_string(position_id):
    if len(position_id) != 14:
        raise ValueError("invalid Position ID")
    try:
        raw = base64.b64decode(position_id + "==", validate=True)
    except Exception:
        raise ValueError("invalid Position ID")
    if len(raw) != 10:
        raise ValueError("invalid Position ID")
    return "".join(chr(65 + (value >> 4)) + chr(65 + (value & 15)) for value in raw)


def _metadata(position_id, match_id):
    from ankigammon.utils.gnuid import parse_gnuid
    from bgsage.data import board_from_gnubg_position_string

    _, metadata = parse_gnuid(position_id + ":" + match_id)
    player = metadata["on_roll"].name.upper()
    owner = metadata["cube_owner"].name.upper()
    match_length = metadata.get("match_length", 0)
    score_x = metadata.get("score_x", 0)
    score_o = metadata.get("score_o", 0)
    player_score = score_x if player == "X" else score_o
    opponent_score = score_o if player == "X" else score_x
    if owner == "CENTERED":
        relative_owner = "centered"
    elif owner == "{}_OWNS".format(player):
        relative_owner = "player"
    else:
        relative_owner = "opponent"
    dice = metadata.get("dice")
    board = board_from_gnubg_position_string(_gnubg_native_string(position_id))
    return {
        "beaver": False if match_length else bool(metadata.get("beavers_allowed", False)),
        "board": list(board),
        "crawford": bool(metadata.get("crawford", False)),
        "cube_owner": relative_owner,
        "cube_value": metadata.get("cube_value", 1),
        "dice": list(dice) if dice is not None else None,
        "jacoby": False if match_length else bool(metadata.get("jacoby", False)),
        "match_length": match_length,
        "on_roll": player,
        "opponent_away": match_length - opponent_score if match_length else 0,
        "opponent_score": opponent_score,
        "player_away": match_length - player_score if match_length else 0,
        "player_score": player_score,
    }


def _move_notation(pre_board, post_board, die1, die2):
    from bgsage.board import possible_single_die_moves

    target = tuple(post_board)
    dice = (die1,) * 4 if die1 == die2 else (die1, die2)

    def find_path(board, remaining, plays):
        if tuple(board) == target:
            return plays
        if not remaining:
            return None
        tried = set()
        for index, die in enumerate(remaining):
            if die in tried:
                continue
            tried.add(die)
            rest = remaining[:index] + remaining[index + 1:]
            for move in possible_single_die_moves(board, die):
                found = find_path(list(move["board"]), rest, plays + [(move["from"], move["to"])])
                if found is not None:
                    return found
        return None

    path = find_path(list(pre_board), dice, [])
    if path is None:
        raise ValueError("BGSage legal-move generator could not reproduce an emitted candidate")
    if not path:
        return "none"

    def point(value):
        return "bar" if value == 25 else "off" if value == 0 else str(value)

    return " ".join("{}/{}".format(point(source), point(destination)) for source, destination in path)


def _probabilities(value):
    return {
        "backgammon_loss": value.backgammon_loss,
        "backgammon_win": value.backgammon_win,
        "gammon_loss": value.gammon_loss,
        "gammon_win": value.gammon_win,
        "win": value.win,
    }


def _legacy_analyzer(bearoff):
    from bgsage import create_analyzer, default_weights

    return create_analyzer(
        level="1ply",
        weights=default_weights(),
        cubeful=True,
        filter_max_moves=5,
        filter_threshold=0.08,
        parallel_threads=1,
        n_trials=1296,
        truncation_depth=0,
        decision_ply=1,
        truncation_ply=-1,
        late_ply=-1,
        late_threshold=20,
        seed=42,
        bearoff_db=str(bearoff),
        ultra_late_threshold=9999,
        cubeful_trial_moves=True,
        cubeful_late_threshold=0,
        prefilter_threshold=0.0,
        target_se=0.0,
        max_batches=50,
    )


def _analyzer(bearoff, analysis_setting, parallel_threads, seed):
    if analysis_setting == "1ply" and parallel_threads == 1 and seed == 42:
        return _legacy_analyzer(bearoff)
    from bgsage import create_analyzer

    # This mirrors the historical Sage-vs-GNU referee: the named level is
    # passed directly to create_analyzer, with cubeful analysis, a fixed seed,
    # explicit thread count, and the packaged bearoff database enabled.
    return create_analyzer(
        level=analysis_setting,
        cubeful=True,
        parallel_threads=parallel_threads,
        bearoff_db=True,
        seed=seed,
    )


def _analysis_configuration(analysis_setting, decision_type, parallel_threads, seed):
    if analysis_setting == "1ply" and parallel_threads == 1 and seed == 42:
        return {
            "candidate_generation": "all-legal-moves" if decision_type == "checker" else "not-applicable",
            "cubeful": True,
            "filter_max_moves": 5,
            "filter_threshold": 0.08,
            "include_game_plans": False,
            "include_two_ply_cube_details": False,
            "model": MODEL,
            "parallel_threads": 1,
            "prefilter_threshold": 0.0,
            "seed": 42,
        }
    return {
        "analysis_setting": analysis_setting,
        "candidate_generation": "all-legal-moves" if decision_type == "checker" else "not-applicable",
        "cubeful": True,
        "include_game_plans": False,
        "include_two_ply_cube_details": False,
        "model": MODEL,
        "parallel_threads": parallel_threads,
        "seed": seed,
    }


def _analyze(payload, identity, bearoff):
    expected = payload.get("expected_identity")
    if not isinstance(expected, dict) or any(identity.get(key) != value for key, value in expected.items()):
        raise ValueError("BGSage runtime identity differs from the request")
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("analysis configuration is required")
    required_common = {
        "cubeful": True,
        "include_game_plans": False,
        "include_two_ply_cube_details": False,
    }
    if any(analysis.get(key) != value for key, value in required_common.items()):
        raise ValueError("unsupported BGSage analysis configuration")
    decision_type = analysis.get("decision_type")
    if decision_type not in ("checker", "cube"):
        raise ValueError("unsupported BGSage decision type")
    analysis_setting = analysis.get("analysis_setting")
    supported = SUPPORTED_CHECKER_SETTINGS if decision_type == "checker" else SUPPORTED_CUBE_SETTINGS
    if analysis_setting not in supported:
        raise ValueError("unsupported BGSage {} setting".format(decision_type))
    parallel_threads = analysis.get("parallel_threads")
    if not isinstance(parallel_threads, int) or isinstance(parallel_threads, bool) or parallel_threads <= 0:
        raise ValueError("parallel_threads must be a positive integer")
    seed = analysis.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    position_id = payload.get("position_id")
    match_id = payload.get("match_id")
    if not isinstance(position_id, str) or not isinstance(match_id, str):
        raise ValueError("combined GNU ID is required")
    context = _metadata(position_id, match_id)
    request_dice = payload.get("dice")
    if decision_type == "checker":
        if request_dice != context["dice"] or not isinstance(request_dice, list) or len(request_dice) != 2:
            raise ValueError("checker dice do not match the GNU Match ID")
    elif request_dice is not None or context["dice"] is not None:
        raise ValueError("cube analysis requires a pre-roll position without dice")

    analyzer = _analyzer(bearoff, analysis_setting, parallel_threads, seed)
    common = {
        "away1": context["player_away"],
        "away2": context["opponent_away"],
        "is_crawford": context["crawford"],
        "jacoby": context["jacoby"],
        "beaver": context["beaver"],
    }
    if decision_type == "checker":
        result = analyzer.checker_play(
            context["board"],
            request_dice[0],
            request_dice[1],
            context["cube_value"],
            context["cube_owner"],
            False,
            None,
            force_boards=None,
            **common
        )
        body = {
            "board": list(result.board),
            "candidate_count": len(result.moves),
            "candidates": [
                {
                    "board": list(move.board),
                    "cubeless_equity": move.cubeless_equity,
                    "equity": move.equity,
                    "equity_difference": move.equity_diff,
                    "eval_level": move.eval_level,
                    "move_notation": _move_notation(
                        result.board, move.board, result.die1, result.die2
                    ),
                    "notation_source": "bgsage.possible_single_die_moves-v1",
                    "probabilities": _probabilities(move.probs),
                    "rank": rank,
                }
                for rank, move in enumerate(result.moves, 1)
            ],
            "dice": [result.die1, result.die2],
            "eval_level": result.eval_level,
            "type": "checker",
        }
    else:
        result = analyzer.cube_action(
            context["board"],
            context["cube_value"],
            context["cube_owner"],
            incl_2ply_details=False,
            **common
        )
        dt_label = "Double/Beaver" if result.is_beaver else "Double/Take"
        body = {
            "actions": [
                {"action": "No Double", "equity": result.equity_nd, "output_order": 1},
                {"action": dt_label, "equity": result.equity_dt, "output_order": 2},
                {"action": "Double/Pass", "equity": result.equity_dp, "output_order": 3},
            ],
            "cubeless_equity": result.cubeless_equity,
            "details": result.details,
            "eval_level": result.eval_level,
            "is_beaver": result.is_beaver,
            "optimal_action": result.optimal_action,
            "optimal_equity": result.optimal_equity,
            "probabilities": _probabilities(result.probs),
            "should_double": result.should_double,
            "should_take": result.should_take,
            "type": "cube",
        }
    return {
        "analysis": body,
        "configuration": _analysis_configuration(
            analysis_setting,
            decision_type,
            parallel_threads,
            seed,
        ),
        "identity": identity,
        "normalized_input": context,
        "protocol": PROTOCOL,
        "request_identity": {
            "analysis_setting": analysis_setting,
            "decision_type": decision_type,
            "match_id": match_id,
            "position_id": position_id,
        },
        "status": "complete",
    }


def main():
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
            raise ValueError("unsupported BGSage protocol")
        identity, bearoff = _runtime_identity()
        operation = payload.get("operation")
        if operation == "identify":
            output = {"identity": identity, "protocol": PROTOCOL, "status": "complete"}
        elif operation == "analyze":
            output = _analyze(payload, identity, bearoff)
        else:
            raise ValueError("unsupported BGSage protocol operation")
        exit_code = 0
    except Exception as exc:
        output = {
            "error": {"code": "protocol_failure", "message": str(exc) or exc.__class__.__name__},
            "protocol": PROTOCOL,
            "status": "failed",
        }
        exit_code = 4
    sys.stdout.write(_canonical(output) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
