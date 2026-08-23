from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.dice import dice_record, namespace_seed, stream_id, stream_sha256
from runner.sage_gnu_campaign.manifests import write_json


REPO = Path(__file__).resolve().parents[2]
FROZEN_CONFIG = load_campaign_config(REPO / "experiments/sage-gnu-campaign-v1/campaign.json")
FROZEN_GNU_SGF_APPLICATION = "GNU Backgammon:" + FROZEN_CONFIG.data["engines"]["gnu"][
    "runtime_identity"
]["engine_version"].split()[0]


def _sgf_point(point: str, seat: str) -> str:
    if point == "bar":
        return "y"
    if point == "off":
        return "z"
    number = int(point)
    return chr(ord("a") + (number - 1 if seat == "O" else 24 - number))


def _game_actions(
    winner: str, points: int, *, seed: str | None = None, game_number: int = 1,
    terminal_kind: str = "ordinary_game_over",
) -> list[dict[str, Any]]:
    loser = "X" if winner == "O" else "O"
    actions: list[dict[str, Any]] = [
        {"action": "checker", "physical_seat": winner, "dice": [3, 1], "moves": [["8", "5"], ["6", "5"]]},
        {"action": "checker", "physical_seat": loser, "dice": [4, 2], "moves": [["13", "9"], ["6", "4"]]},
    ]
    cube_value = 1
    if terminal_kind == "drop":
        if points != 1:
            raise ValueError("drop fixture currently requires one point")
        actions.extend([
            {"action": "double", "physical_seat": winner, "cube_value": 2},
            {"action": "drop", "physical_seat": loser},
        ])
        cube_value = 1
    elif points > 3:
        actions.extend([
            {"action": "double", "physical_seat": winner, "cube_value": 2},
            {"action": "take", "physical_seat": loser},
        ])
        cube_value = 2
    if points > 6:
        actions.extend([
            {"action": "checker", "physical_seat": winner, "dice": [5, 2], "moves": [["13", "8"], ["8", "6"]]},
            {"action": "double", "physical_seat": loser, "cube_value": 4},
            {"action": "take", "physical_seat": winner},
            {"action": "checker", "physical_seat": loser, "dice": [2, 1], "moves": [["9", "7"], ["7", "6"]]},
        ])
        cube_value = 4
    if points % cube_value or not 1 <= points // cube_value <= 3:
        raise ValueError("fixture result is not representable by a normal GNU game")
    if terminal_kind == "ordinary_game_over":
        actions.append({
            "action": "checker", "physical_seat": winner, "dice": [6, 5],
            "moves": [["6", "off"], ["5", "off"]],
        })
    elif terminal_kind not in {"drop", "resignation"}:
        raise ValueError("unsupported fixture terminal kind")
    if seed is not None:
        checker_indexes = {"O": 0, "X": 0}
        opening_index = 1
        while True:
            o_opening = dice_record(seed, 1, 7, game_number, "O", opening_index)["opening_die"]
            x_opening = dice_record(seed, 1, 7, game_number, "X", opening_index)["opening_die"]
            if o_opening != x_opening:
                break
            opening_index += 1
        opener = "O" if o_opening > x_opening else "X"
        if actions[0]["physical_seat"] != opener:
            # Rotate the ordinary fixture's actor pattern without changing the
            # terminal winner.
            actions[:0] = [
                {"action": "checker", "physical_seat": opener, "moves": [["24", "21"], ["13", "11"]]},
            ]
        actions[0]["dice"] = [
            o_opening if opener == "O" else x_opening,
            x_opening if opener == "O" else o_opening,
        ]
        for action in actions[1:]:
            if action["action"] != "checker":
                continue
            seat = action["physical_seat"]
            checker_indexes[seat] += 1
            row = dice_record(seed, 1, 7, game_number, seat, checker_indexes[seat])
            action["dice"] = [row["die1"], row["die2"]]
    return actions


def _sgf_action(action: dict[str, Any]) -> str:
    seat = action["physical_seat"]
    prop = "W" if seat == "O" else "B"
    if action["action"] != "checker":
        return f";{prop}[{action['action']}]"
    encoded = "".join(str(die) for die in action["dice"])
    encoded += "".join(
        _sgf_point(point, seat) for move in action["moves"] for point in move
    )
    return f";{prop}[{encoded}]"


def _text_action(action: dict[str, Any]) -> str:
    if action["action"] == "checker":
        dice = "".join(str(die) for die in action["dice"])
        moves = " ".join(f"{source}/{destination}" for source, destination in action["moves"])
        return f"{dice}: {moves}"
    return {
        "double": f"Doubles => {action.get('cube_value', 2)}",
        "take": "Takes", "drop": "Drops",
    }[action["action"]]


def _text_action_rows(actions: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    row_number = 1
    pending_left: str | None = None
    for action in actions:
        rendered = _text_action(action)
        if action["physical_seat"] == "O":
            if pending_left is not None:
                rows.append(f"{row_number:3d}) {pending_left}\n")
                row_number += 1
            pending_left = rendered
        else:
            left = pending_left or ""
            rows.append(f"{row_number:3d}) {left:<27} {rendered}\n")
            pending_left = None
            row_number += 1
    if pending_left is not None:
        rows.append(f"{row_number:3d}) {pending_left}\n")
    return rows


def native_documents(
    engine_by_seat: dict[str, str], games: list[tuple[str, int]], *, seed: str | None = None,
    terminal_kinds: list[str] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    score = [0, 0]
    sgf: list[str] = []
    text = ["7 point match\n"]
    summaries: list[dict[str, Any]] = []
    terminal_kinds = terminal_kinds or ["ordinary_game_over"] * len(games)
    if len(terminal_kinds) != len(games):
        raise ValueError("fixture terminal kind count differs from games")
    for index, (winner, points) in enumerate(games):
        o_name = f"{engine_by_seat['O']}_seat_O"
        x_name = f"{engine_by_seat['X']}_seat_X"
        terminal_kind = terminal_kinds[index]
        actions = _game_actions(
            winner, points, seed=seed, game_number=index + 1, terminal_kind=terminal_kind,
        )
        sgf.append(
            f"(;FF[4]GM[6]AP[{FROZEN_GNU_SGF_APPLICATION}]"
            f"MI[length:7][game:{index}][ws:{score[0]}][bs:{score[1]}]"
            f"PW[{o_name}]PB[{x_name}]RE[{'W' if winner == 'O' else 'B'}+{points}"
            f"{'R' if terminal_kind == 'resignation' else ''}]"
            f"{''.join(_sgf_action(action) for action in actions)})\n"
        )
        text.append(
            f"\n Game {index + 1}\n {o_name} : {score[0]}             {x_name} : {score[1]}\n"
        )
        text.extend(_text_action_rows(actions))
        text.append(f"{'      ' if winner == 'O' else '                                  '}Wins {points} points\n")
        start_score = list(score)
        score[0 if winner == "O" else 1] += points
        summaries.append({"start_score": start_score, "post_score": list(score)})
    sgf_document = "".join(sgf)
    text_document = "".join(text)
    # Use the production parser so fixture manifests carry the exact canonical
    # native evidence that publication will revalidate.
    from runner.sage_gnu_campaign.match import _parse_sgf_match

    parsed = _parse_sgf_match(
        sgf_document.strip(), engine_by_seat, FROZEN_GNU_SGF_APPLICATION
    )
    summary = {"game_count": len(parsed), "games": parsed, "final_score": parsed[-1]["post_score"]}
    return sgf_document, text_document, summary


def write_complete_match(
    match: Path,
    engine_by_seat: dict[str, str],
    games: list[tuple[str, int]] | None = None,
    *,
    identity: Any,
    side: str,
    terminal_kinds: list[str] | None = None,
) -> dict[str, Any]:
    games = games or [("O", 8)]
    seed = namespace_seed(identity.base_seed, side)
    match.mkdir(parents=True, exist_ok=True)
    native = match / "native"
    native.mkdir()
    sgf, text, summary = native_documents(
        engine_by_seat, games, seed=seed, terminal_kinds=terminal_kinds,
    )
    (native / "match.sgf").write_text(sgf, encoding="utf-8")
    (native / "match.txt").write_text(text, encoding="utf-8")

    decisions: list[dict[str, Any]] = []
    for game in summary["games"]:
        for action_index, action in enumerate(game["actions"], 1):
            terminal = (
                action_index == len(game["actions"])
                and game["terminal"]["kind"] != "resignation"
            )
            seat = action["physical_seat"]
            action_type = action["action"]
            command = (
                " ".join(f"{source}/{destination}" for source, destination in action["moves"])
                if action_type == "checker"
                else {"double": "double", "take": "take", "drop": "pass"}[action_type]
            )
            event = None
            if terminal:
                event = {
                    "kind": game["terminal"]["kind"],
                    "winner_physical_seat": game["winner_physical_seat"],
                    "winner_engine": game["winner_engine"],
                    "points": game["points"],
                    "result_level": game["terminal"]["result_level"],
                }
                if event["kind"] == "drop":
                    event.update({"loser_physical_seat": seat, "loser_engine": engine_by_seat[seat]})
                elif event["kind"] == "resignation":
                    event["resignation_level"] = game["terminal"]["result_level"]
            ordinal = len(decisions) + 1
            pre_gnuid = f"game-{game['game_number']}-record-{action_index}-pre"
            post_gnuid = f"game-{game['game_number']}-record-{action_index}-post"
            subsequent = None
            if terminal and game["game_number"] < summary["game_count"]:
                next_game = summary["games"][game["game_number"]]
                post_gnuid = f"game-{game['game_number'] + 1}-opening"
                subsequent = {
                    "game_number": game["game_number"] + 1,
                    "gnuid": post_gnuid,
                    "score": game["post_score"],
                    "on_roll_physical_seat": next_game["opening_state"]["on_roll_physical_seat"],
                    "decision_physical_seat": next_game["opening_state"]["on_roll_physical_seat"],
                    "dice": next_game["opening_state"]["dice"],
                }
            decisions.append({
                "campaign_id": identity.campaign_id,
                "pair_id": identity.pair_id,
                "pair_index": identity.pair_index,
                "pair_member": side,
                "match_side": side,
                "record_ordinal": ordinal,
                "decision_ordinal": ordinal,
                "game_number": game["game_number"],
                "physical_seat": seat,
                "engine": engine_by_seat[seat],
                "gnuid": pre_gnuid,
                "decision_type": "checker" if action_type == "checker" else "cube",
                "analysis_dice": action["dice"] if action_type == "checker" else None,
                "command": command,
                "engine_kit_result": {"status": "fixture"},
                "transition_evidence": {
                    "command_type": "checker" if action_type == "checker" else command,
                    "acting_physical_seat": seat,
                    "acting_engine": engine_by_seat[seat],
                    "pre_command": {"gnuid": pre_gnuid, "score": game["start_score"]},
                    "terminal_event": event,
                    "post_command": {
                        "gnuid": post_gnuid,
                        "score": game["post_score"] if terminal else game["start_score"],
                    },
                    "game_number": game["game_number"],
                    "subsequent_opening_state": subsequent,
                },
            })
        if game["terminal"]["kind"] == "resignation":
            winner = game["winner_physical_seat"]
            ordinal = len(decisions) + 1
            pre_gnuid = f"game-{game['game_number']}-resignation-pre"
            post_gnuid = f"game-{game['game_number']}-resignation-post"
            subsequent = None
            if game["game_number"] < summary["game_count"]:
                next_game = summary["games"][game["game_number"]]
                post_gnuid = f"game-{game['game_number'] + 1}-opening"
                subsequent = {
                    "game_number": game["game_number"] + 1,
                    "gnuid": post_gnuid,
                    "score": game["post_score"],
                    "on_roll_physical_seat": next_game["opening_state"]["on_roll_physical_seat"],
                    "decision_physical_seat": next_game["opening_state"]["on_roll_physical_seat"],
                    "dice": next_game["opening_state"]["dice"],
                }
            decisions.append({
                "campaign_id": identity.campaign_id,
                "pair_id": identity.pair_id,
                "pair_index": identity.pair_index,
                "pair_member": side,
                "match_side": side,
                "record_ordinal": ordinal,
                "decision_ordinal": ordinal,
                "game_number": game["game_number"],
                "physical_seat": winner,
                "engine": engine_by_seat[winner],
                "gnuid": pre_gnuid,
                "decision_type": "board-rule",
                "analysis_dice": None,
                "command": "accept",
                "engine_kit_result": {"status": "board-rule", "action": "accept-resignation"},
                "transition_evidence": {
                    "command_type": "accepted_resignation",
                    "acting_physical_seat": winner,
                    "acting_engine": engine_by_seat[winner],
                    "pre_command": {"gnuid": pre_gnuid, "score": game["start_score"]},
                    "terminal_event": _fixture_terminal_event(game, engine_by_seat),
                    "post_command": {"gnuid": post_gnuid, "score": game["post_score"]},
                    "game_number": game["game_number"],
                    "subsequent_opening_state": subsequent,
                },
            })
    for index in range(1, len(decisions)):
        opening = decisions[index - 1]["transition_evidence"]["subsequent_opening_state"]
        if opening is not None:
            decisions[index]["gnuid"] = opening["gnuid"]
            decisions[index]["transition_evidence"]["pre_command"]["gnuid"] = opening["gnuid"]
    (match / "decisions.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )

    dice = match / "dice"
    dice.mkdir()
    consumption_records: list[dict[str, Any]] = []
    for game in summary["games"]:
        game_number = game["game_number"]
        opening_index = 1
        while True:
            values = {
                seat: dice_record(seed, 1, 7, game_number, seat, opening_index)["opening_die"]
                for seat in ("O", "X")
            }
            for seat in ("O", "X"):
                consumption_records.append(_consumption_record(
                    identity, side, seed, engine_by_seat, game_number, "opening",
                    opening_index, seat, values[seat], None, len(consumption_records) + 1,
                ))
            if values["O"] != values["X"]:
                break
            opening_index += 1
        checker_indexes = {"O": 0, "X": 0}
        for action in game["actions"][1:]:
            if action["action"] != "checker":
                continue
            seat = action["physical_seat"]
            checker_indexes[seat] += 1
            row = dice_record(seed, 1, 7, game_number, seat, checker_indexes[seat])
            consumption_records.append(_consumption_record(
                identity, side, seed, engine_by_seat, game_number, "checker",
                checker_indexes[seat], seat, row["die1"], row["die2"],
                len(consumption_records) + 1,
            ))
    consumption = dice / "seat_dice_consumption.jsonl"
    consumption.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in consumption_records),
        encoding="utf-8",
    )
    write_json(dice / "seat_dice_manifest.json", {
        "schema_version": "physical-seat-dice-stream-v1",
        "namespace": side,
        "namespace_seed": seed,
        "base_seed": identity.base_seed,
        "pair_id": identity.pair_id,
        "roll_count": 50000,
        "files_per_match": 25,
        "engine_by_physical_seat": engine_by_seat,
        "streams": [
            {
                "namespace": side,
                "namespace_seed": seed,
                "base_seed": identity.base_seed,
                "pair_id": identity.pair_id,
                "pair_member": side,
                "match_side": side,
                "game_number": game_number,
                "physical_seat": seat,
                "engine": engine_by_seat[seat],
                "stream_id": stream_id(seed, game_number, seat),
                "path": f"game_{game_number:03d}_seat_{seat}.csv",
                "sha256": stream_sha256(seed, game_number, seat, 50000),
            }
            for game_number in range(1, len(games) + 1)
            for seat in ("O", "X")
        ],
        "consumption": {
            "path": consumption.name,
            "entries": len(consumption_records),
            "sha256": hashlib.sha256(consumption.read_bytes()).hexdigest(),
        },
    })
    manifest = {
        "side": side,
        "pair_member": side,
        "engine_by_physical_seat": engine_by_seat,
        "namespace_seed": seed,
        "dice_manifest": "dice/seat_dice_manifest.json",
        "dice_consumption": "dice/seat_dice_consumption.jsonl",
        "candidate_actual_depth_evidence": "decisions.jsonl",
        "native_outputs": ["native/match.sgf", "native/match.txt"],
        "native_evidence": summary,
    }
    write_json(match / "match_manifest.json", manifest)
    return manifest


def _consumption_record(
    identity: Any, side: str, seed: str, engine_by_seat: dict[str, str],
    game_number: int, prompt_type: str, roll_index: int, seat: str,
    die1: int, die2: int | None, ordinal: int,
) -> dict[str, Any]:
    return {
        "schema_version": "physical-seat-dice-stream-v1",
        "namespace": side,
        "namespace_seed": seed,
        "base_seed": identity.base_seed,
        "pair_id": identity.pair_id,
        "pair_member": side,
        "match_side": side,
        "consumption_ordinal": ordinal,
        "game_number": game_number,
        "prompt_type": prompt_type,
        "roll_index": roll_index,
        "physical_seat": seat,
        "engine": engine_by_seat[seat],
        "die1": die1,
        "die2": die2,
        "stream_id": stream_id(seed, game_number, seat),
        "stream_path": f"game_{game_number:03d}_seat_{seat}.csv",
    }


def _fixture_terminal_event(
    game: dict[str, Any], engine_by_seat: dict[str, str],
) -> dict[str, Any]:
    winner = game["winner_physical_seat"]
    event = {
        "kind": game["terminal"]["kind"],
        "winner_physical_seat": winner,
        "winner_engine": engine_by_seat[winner],
        "points": game["points"],
        "result_level": game["terminal"]["result_level"],
    }
    if event["kind"] == "resignation":
        event["resignation_level"] = game["terminal"]["result_level"]
    elif event["kind"] == "drop":
        loser = "X" if winner == "O" else "O"
        event.update({"loser_physical_seat": loser, "loser_engine": engine_by_seat[loser]})
    return event


def write_execution_fixture(
    root: Path,
    identity: Any,
    games: list[tuple[str, int]] | None = None,
    terminal_kinds: list[str] | None = None,
) -> None:
    matches = [
        write_complete_match(
            root / "matches/A", {"O": "sage", "X": "gnu"}, games,
            identity=identity, side="A", terminal_kinds=terminal_kinds,
        ),
        write_complete_match(
            root / "matches/B", {"O": "gnu", "X": "sage"}, games,
            identity=identity, side="B", terminal_kinds=terminal_kinds,
        ),
    ]
    write_json(root / "execution_result.json", {
        "status": "complete",
        "pair_identity": identity.to_dict(),
        "matches": matches,
    })
