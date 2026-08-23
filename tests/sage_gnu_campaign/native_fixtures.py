from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runner.sage_gnu_campaign.manifests import write_json


def _sgf_point(point: str, seat: str) -> str:
    if point == "bar":
        return "y"
    if point == "off":
        return "z"
    number = int(point)
    return chr(ord("a") + (number - 1 if seat == "O" else 24 - number))


def _game_actions(winner: str, points: int) -> list[dict[str, Any]]:
    loser = "X" if winner == "O" else "O"
    actions: list[dict[str, Any]] = [
        {"action": "checker", "physical_seat": winner, "dice": [3, 1], "moves": [["8", "5"], ["6", "5"]]},
        {"action": "checker", "physical_seat": loser, "dice": [4, 2], "moves": [["13", "9"], ["6", "4"]]},
    ]
    cube_value = 1
    if points > 3:
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
    actions.append({
        "action": "checker", "physical_seat": winner, "dice": [6, 5],
        "moves": [["6", "off"], ["5", "off"]],
    })
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
    engine_by_seat: dict[str, str], games: list[tuple[str, int]],
) -> tuple[str, str, dict[str, Any]]:
    score = [0, 0]
    sgf: list[str] = []
    text = ["7 point match\n"]
    summaries: list[dict[str, Any]] = []
    for index, (winner, points) in enumerate(games):
        o_name = f"{engine_by_seat['O']}_seat_O"
        x_name = f"{engine_by_seat['X']}_seat_X"
        actions = _game_actions(winner, points)
        sgf.append(
            "(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]"
            f"MI[length:7][game:{index}][ws:{score[0]}][bs:{score[1]}]"
            f"PW[{o_name}]PB[{x_name}]RE[{'W' if winner == 'O' else 'B'}+{points}]"
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

    parsed = _parse_sgf_match(sgf_document.strip(), engine_by_seat)
    summary = {"game_count": len(parsed), "games": parsed, "final_score": parsed[-1]["post_score"]}
    return sgf_document, text_document, summary


def write_complete_match(
    match: Path,
    engine_by_seat: dict[str, str],
    games: list[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    games = games or [("O", 8)]
    match.mkdir(parents=True, exist_ok=True)
    native = match / "native"
    native.mkdir()
    sgf, text, summary = native_documents(engine_by_seat, games)
    (native / "match.sgf").write_text(sgf, encoding="utf-8")
    (native / "match.txt").write_text(text, encoding="utf-8")

    decisions = []
    for game in summary["games"]:
        seat = game["winner_physical_seat"]
        decisions.append({
            "game_number": game["game_number"],
            "physical_seat": seat,
            "engine": engine_by_seat[seat],
            "command": "1/off",
            "transition_evidence": {
                "game_number": game["game_number"],
                "terminal_event": {
                    "kind": "ordinary_game_over",
                    "winner_physical_seat": seat,
                    "winner_engine": engine_by_seat[seat],
                    "points": game["points"],
                    "result_level": 1,
                },
                "post_command": {"gnuid": f"post-{game['game_number']}", "score": game["post_score"]},
            },
        })
    (match / "decisions.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )

    dice = match / "dice"
    dice.mkdir()
    consumption_records = [
        {
            "game_number": game_number,
            "prompt_type": "opening",
            "physical_seat": seat,
            "engine": engine_by_seat[seat],
            "roll_index": 1,
            "die1": 4 if seat == "O" else 2,
            "die2": None,
        }
        for game_number in range(1, len(games) + 1)
        for seat in ("O", "X")
    ]
    consumption = dice / "seat_dice_consumption.jsonl"
    consumption.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in consumption_records),
        encoding="utf-8",
    )
    write_json(dice / "seat_dice_manifest.json", {
        "engine_by_physical_seat": engine_by_seat,
        "streams": [
            {
                "game_number": game_number,
                "physical_seat": seat,
                "engine": engine_by_seat[seat],
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
        "side": match.name,
        "pair_member": match.name,
        "engine_by_physical_seat": engine_by_seat,
        "dice_manifest": "dice/seat_dice_manifest.json",
        "dice_consumption": "dice/seat_dice_consumption.jsonl",
        "candidate_actual_depth_evidence": "decisions.jsonl",
        "native_outputs": ["native/match.sgf", "native/match.txt"],
        "native_evidence": summary,
    }
    write_json(match / "match_manifest.json", manifest)
    return manifest


def write_execution_fixture(
    root: Path,
    identity: Any,
    games: list[tuple[str, int]] | None = None,
) -> None:
    matches = [
        write_complete_match(root / "matches/A", {"O": "sage", "X": "gnu"}, games),
        write_complete_match(root / "matches/B", {"O": "gnu", "X": "sage"}, games),
    ]
    write_json(root / "execution_result.json", {
        "status": "complete",
        "pair_identity": identity.to_dict(),
        "matches": matches,
    })
