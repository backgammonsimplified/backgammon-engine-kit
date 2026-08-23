from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runner.sage_gnu_campaign.manifests import write_json


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
        sgf.append(
            "(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]"
            f"MI[length:7][game:{index}][ws:{score[0]}][bs:{score[1]}]"
            f"PW[{o_name}]PB[{x_name}]RE[{'W' if winner == 'O' else 'B'}+{points}])\n"
        )
        text.append(
            f"\n Game {index + 1}\n {o_name} : {score[0]}             {x_name} : {score[1]}\n"
            f"{'      ' if winner == 'O' else '                                    '}Wins {points} points\n"
        )
        start_score = list(score)
        score[0 if winner == "O" else 1] += points
        summaries.append({
            "game_number": index + 1,
            "start_score": start_score,
            "winner_physical_seat": winner,
            "winner_engine": engine_by_seat[winner],
            "points": points,
            "post_score": list(score),
        })
    summary = {"game_count": len(games), "games": summaries, "final_score": list(score)}
    return "".join(sgf), "".join(text), summary


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
