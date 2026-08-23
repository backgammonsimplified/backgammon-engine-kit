from __future__ import annotations

import hashlib
import base64
import copy
import math
import json
from pathlib import Path
from typing import Any

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.dice import (
    dice_record,
    namespace_seed,
    stream_content,
    stream_id,
    stream_sha256,
)
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
    if seed is not None:
        opening_index = 1
        while True:
            o_opening = dice_record(seed, 1, 7, game_number, "O", opening_index)["opening_die"]
            x_opening = dice_record(seed, 1, 7, game_number, "X", opening_index)["opening_die"]
            if o_opening != x_opening:
                break
            opening_index += 1
        opener = "O" if o_opening > x_opening else "X"
        actions: list[dict[str, Any]] = [{"action": "checker", "physical_seat": opener}]
        current = "X" if opener == "O" else "O"
        cube_value = 1
        cube_owner = "center"
        if terminal_kind == "drop":
            if points != 1:
                raise ValueError("drop fixture currently requires one point")
            while current != winner:
                actions.append({"action": "checker", "physical_seat": current})
                current = "X" if current == "O" else "O"
            actions.extend([
                {"action": "double", "physical_seat": winner, "cube_value": 2},
                {"action": "drop", "physical_seat": loser},
            ])
        elif terminal_kind == "resignation":
            target_cube = next(
                (cube for cube in (4, 2, 1) if points % cube == 0 and 1 <= points // cube <= 3),
                None,
            )
            if target_cube is None:
                raise ValueError("resignation fixture result is not representable")
            while cube_value < target_cube:
                if cube_owner != "center" and cube_owner != current:
                    actions.append({"action": "checker", "physical_seat": current})
                    current = "X" if current == "O" else "O"
                actions.extend([
                    {"action": "double", "physical_seat": current, "cube_value": cube_value * 2},
                    {"action": "take", "physical_seat": "X" if current == "O" else "O"},
                ])
                cube_value *= 2
                cube_owner = "X" if current == "O" else "O"
                if cube_value < target_cube:
                    actions.append({"action": "checker", "physical_seat": current})
                    current = "X" if current == "O" else "O"
            while current != loser:
                actions.append({"action": "checker", "physical_seat": current})
                current = "X" if current == "O" else "O"
        elif terminal_kind == "ordinary_game_over":
            target_cube = next(
                (cube for cube in (4, 2, 1) if points % cube == 0 and 1 <= points // cube <= 3),
                None,
            )
            if target_cube is None:
                raise ValueError("fixture result is not representable by a normal GNU game")
            while cube_value < target_cube:
                if cube_owner != "center" and cube_owner != current:
                    actions.append({"action": "checker", "physical_seat": current})
                    current = "X" if current == "O" else "O"
                actions.extend([
                    {"action": "double", "physical_seat": current, "cube_value": cube_value * 2},
                    {"action": "take", "physical_seat": "X" if current == "O" else "O"},
                ])
                cube_value *= 2
                cube_owner = "X" if current == "O" else "O"
                if cube_value < target_cube or current != winner:
                    actions.append({"action": "checker", "physical_seat": current})
                    current = "X" if current == "O" else "O"
            while current != winner:
                actions.append({"action": "checker", "physical_seat": current})
                current = "X" if current == "O" else "O"
            actions.append({"action": "checker", "physical_seat": winner})
        else:
            raise ValueError("unsupported fixture terminal kind")

        checker_indexes = {"O": 0, "X": 0}
        for index, action in enumerate(actions):
            if action["action"] != "checker":
                continue
            seat = action["physical_seat"]
            if index == 0:
                dice = [
                    o_opening if opener == "O" else x_opening,
                    x_opening if opener == "O" else o_opening,
                ]
            else:
                checker_indexes[seat] += 1
                row = dice_record(seed, 1, 7, game_number, seat, checker_indexes[seat])
                dice = [row["die1"], row["die2"]]
            # Frozen GNU stores MatchID/native dice in descending order.  The
            # deterministic consumption journal below intentionally retains
            # the generator's original die1/die2 stream order.
            action["dice"] = sorted(dice, reverse=True)

        checker_actions = {
            seat: [action for action in actions if action["action"] == "checker" and action["physical_seat"] == seat]
            for seat in ("O", "X")
        }
        if terminal_kind == "ordinary_game_over":
            for seat, seat_actions in checker_actions.items():
                terminal_seat = seat == winner
                source = sum(
                    sum(action["dice"]) if action["dice"][0] != action["dice"][1]
                    else action["dice"][0] * 4
                    for action in seat_actions
                ) + (0 if terminal_seat else 1)
                if not 1 <= source <= 24:
                    raise ValueError("fixture checker path exceeds the board")
                for action in seat_actions:
                    dice_to_play = (
                        action["dice"]
                        if action["dice"][0] != action["dice"][1]
                        else action["dice"] * 2
                    )
                    moves = []
                    for die in dice_to_play:
                        destination = source - die
                        moves.append([str(source), "off" if destination == 0 else str(destination)])
                        source = destination
                    action["moves"] = moves
        else:
            from types import SimpleNamespace

            from runner.sage_gnu_campaign.match import _legal_checker_plays

            fixture_players = _standard_players()
            for action in actions:
                if action["action"] != "checker":
                    continue
                board = SimpleNamespace(
                    checker_count=SimpleNamespace(player_0=15, player_1=15),
                    player_0=SimpleNamespace(**fixture_players["O"]),
                    player_1=SimpleNamespace(**fixture_players["X"]),
                )
                position = SimpleNamespace(
                    board=board, state=SimpleNamespace(dice=tuple(action["dice"]))
                )
                legal = _legal_checker_plays(position, action["physical_seat"])
                action["moves"] = [list(move) for move in legal[0][1]]
                _apply_fixture_checker(
                    {"players": fixture_players}, action["physical_seat"], action["moves"]
                )
        return actions

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


def _sgf_setup_node(players: dict[str, dict[str, Any]], on_roll: str) -> str:
    rendered = [f";PL[{'W' if on_roll == 'O' else 'B'}]AE[a:y]"]
    for seat, property_name in (("O", "AW"), ("X", "AB")):
        values = [
            _sgf_point(str(point), seat)
            for point, count in enumerate(players[seat]["points"], 1)
            for _ in range(count)
        ] + ["y"] * players[seat]["bar"]
        if values:
            rendered.append(property_name + "".join(f"[{value}]" for value in values))
    return "".join(rendered)


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
        setup_node = ""
        if seed is not None and terminal_kind == "ordinary_game_over":
            cube_value = max(
                [action.get("cube_value", 1) for action in actions if action["action"] == "double"],
                default=1,
            )
            setup_game = {
                "winner_physical_seat": winner,
                "points": points,
                "terminal": {"result_level": points // cube_value},
            }
            setup_node = _sgf_setup_node(
                _ordinary_fixture_players(setup_game, actions), actions[0]["physical_seat"]
            )
        sgf.append(
            f"(;FF[4]GM[6]AP[{FROZEN_GNU_SGF_APPLICATION}]"
            f"MI[length:7][game:{index}][ws:{score[0]}][bs:{score[1]}]"
            f"PW[{o_name}]PB[{x_name}]RU[Crawford]RE[{'W' if winner == 'O' else 'B'}+{points}"
            f"{'R' if terminal_kind == 'resignation' else ''}]"
            f"{setup_node}{''.join(_sgf_action(action) for action in actions)})\n"
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


def _set_bits(bits: list[int], start: int, width: int, value: int) -> None:
    for offset in range(width):
        bits[start + offset] = (value >> offset) & 1


def _encode_fixture_gnuid(state: dict[str, Any]) -> str:
    on_roll = state["on_roll"]
    blocks = [state["players"]["X"], state["players"]["O"]] if on_roll == "O" else [
        state["players"]["O"], state["players"]["X"]
    ]
    position_bits: list[int] = []
    for board in blocks:
        for count in [*board["points"], board["bar"]]:
            position_bits.extend([1] * count)
            position_bits.append(0)
    position_bits.extend([0] * (80 - len(position_bits)))
    position_bytes = bytes(
        sum(position_bits[index * 8 + bit] << bit for bit in range(8))
        for index in range(10)
    )

    bits = [0] * 72
    _set_bits(bits, 0, 4, int(math.log2(state["cube_value"])))
    _set_bits(bits, 4, 2, {"O": 0, "X": 1, "center": 3}[state["cube_owner"]])
    _set_bits(bits, 6, 1, 0 if on_roll == "O" else 1)
    _set_bits(bits, 7, 1, 0)
    _set_bits(bits, 8, 3, {"setup": 0, "playing": 1, "game_over": 2, "resigned": 3}[state["game_state"]])
    decision = state["decision"] or on_roll
    _set_bits(bits, 11, 1, 0 if decision == "O" else 1)
    pending = state["pending"]
    _set_bits(bits, 12, 1, int(pending["type"] == "double"))
    _set_bits(bits, 13, 2, pending.get("multiplier", 0) if pending["type"] == "resignation" else 0)
    dice = state["dice"] or [0, 0]
    _set_bits(bits, 15, 3, dice[0])
    _set_bits(bits, 18, 3, dice[1])
    _set_bits(bits, 21, 15, 7)
    _set_bits(bits, 36, 15, state["score"][0])
    _set_bits(bits, 51, 15, state["score"][1])
    bits[66] = 1
    match_bytes = bytes(
        sum(bits[index * 8 + bit] << bit for bit in range(8))
        for index in range(9)
    )
    return ":".join(
        base64.b64encode(value).decode("ascii").rstrip("=")
        for value in (position_bytes, match_bytes)
    )


def _standard_players() -> dict[str, dict[str, Any]]:
    points = [0] * 24
    for point, count in ((6, 5), (8, 3), (13, 5), (24, 2)):
        points[point - 1] = count
    return {
        seat: {"points": list(points), "bar": 0, "off": 0}
        for seat in ("O", "X")
    }


def _ordinary_fixture_players(
    game: dict[str, Any], actions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    winner = game["winner_physical_seat"]
    loser = "X" if winner == "O" else "O"
    cube = max(
        [action.get("cube_value", 1) for action in actions if action["action"] == "double"],
        default=1,
    )
    level = game["points"] // cube
    first_source = {
        seat: int(next(
            action["moves"][0][0] for action in actions
            if action["action"] == "checker" and action["physical_seat"] == seat
        ))
        for seat in ("O", "X")
    }
    players = {
        seat: {"points": [0] * 24, "bar": 0, "off": 0}
        for seat in ("O", "X")
    }
    players[winner]["points"][first_source[winner] - 1] = 1
    players[winner]["off"] = 14
    loser_off = 1 if level == 1 else 0
    loser_bar = 1 if level == 3 else 0
    players[loser]["off"] = loser_off
    players[loser]["bar"] = loser_bar
    players[loser]["points"][first_source[loser] - 1] += 1
    players[loser]["points"][0] += 15 - loser_off - loser_bar - 1
    return players


def _apply_fixture_checker(state: dict[str, Any], seat: str, moves: list[list[str]]) -> None:
    actor = state["players"][seat]
    opponent = state["players"]["X" if seat == "O" else "O"]
    for source, destination in moves:
        if source == "bar":
            actor["bar"] -= 1
        else:
            actor["points"][int(source) - 1] -= 1
        if destination == "off":
            actor["off"] += 1
            continue
        target = int(destination)
        opponent_index = 24 - target
        if opponent["points"][opponent_index] == 1:
            opponent["points"][opponent_index] = 0
            opponent["bar"] += 1
        actor["points"][target - 1] += 1


def _next_opening_state(
    game: dict[str, Any], score: list[int],
) -> dict[str, Any]:
    opening = game["opening_state"]
    opener = opening["on_roll_physical_seat"]
    return {
        "players": _standard_players(), "on_roll": opener, "decision": opener,
        "dice": list(opening["dice"]), "cube_value": 1, "cube_owner": "center",
        "pending": {"type": "none"}, "score": list(score), "game_state": "playing",
    }


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
    effective_terminal_kinds = terminal_kinds or ["ordinary_game_over"] * len(games)
    if len(games) > 1:
        # Multi-game fixtures exercise automatic next-game openings with legal
        # starting boards; ordinary bearoff remains covered by the one-game fixture.
        effective_terminal_kinds = [
            "resignation" if kind == "ordinary_game_over" else kind
            for kind in effective_terminal_kinds
        ]
    sgf, text, summary = native_documents(
        engine_by_seat, games, seed=seed, terminal_kinds=effective_terminal_kinds,
    )
    (native / "match.sgf").write_text(sgf, encoding="utf-8")
    (native / "match.txt").write_text(text, encoding="utf-8")

    dice = match / "dice"
    dice.mkdir()
    for game_number in range(1, 26):
        for seat in ("O", "X"):
            filename = f"game_{game_number:03d}_seat_{seat}.csv"
            expected_content = stream_content(seed, game_number, seat, 50000)
            (dice / filename).write_bytes(expected_content)
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

    decisions: list[dict[str, Any]] = []

    def append_decision(
        game: dict[str, Any], seat: str, decision_type: str, command: str,
        command_type: str, pre_state: dict[str, Any], post_state: dict[str, Any],
        *, analysis_dice: list[int] | None = None,
        event: dict[str, Any] | None = None,
        subsequent: dict[str, Any] | None = None,
    ) -> None:
        ordinal = len(decisions) + 1
        pre_gnuid = _encode_fixture_gnuid(pre_state)
        post_gnuid = _encode_fixture_gnuid(post_state)
        decisions.append({
            "campaign_id": identity.campaign_id, "pair_id": identity.pair_id,
            "pair_index": identity.pair_index, "pair_member": side, "match_side": side,
            "record_ordinal": ordinal, "decision_ordinal": ordinal,
            "game_number": game["game_number"], "physical_seat": seat,
            "engine": engine_by_seat[seat], "gnuid": pre_gnuid,
            "decision_type": decision_type, "analysis_dice": analysis_dice,
            "command": command,
            "engine_kit_result": (
                {"status": "board-rule", "action": "accept-resignation"}
                if decision_type == "board-rule" else {"status": "fixture"}
            ),
            "transition_evidence": {
                "command_type": command_type, "acting_physical_seat": seat,
                "acting_engine": engine_by_seat[seat],
                "pre_command": {"gnuid": pre_gnuid, "score": list(pre_state["score"])},
                "terminal_event": event,
                "post_command": {"gnuid": post_gnuid, "score": list(post_state["score"])},
                "game_number": game["game_number"],
                "subsequent_opening_state": subsequent,
            },
        })

    current: dict[str, Any] | None = None
    for game in summary["games"]:
        actions = game["actions"]
        if current is None:
            opening = game["opening_state"]
            opener = opening["on_roll_physical_seat"]
            players = (
                _ordinary_fixture_players(game, actions)
                if game["terminal"]["kind"] == "ordinary_game_over"
                else _standard_players()
            )
            current = {
                "players": players, "on_roll": opener, "decision": opener,
                "dice": list(opening["dice"]), "cube_value": 1, "cube_owner": "center",
                "pending": {"type": "none"}, "score": list(game["start_score"]),
                "game_state": "playing",
            }
        for action_index, action in enumerate(actions):
            seat = action["physical_seat"]
            action_type = action["action"]
            if action_type == "checker" and action_index > 0:
                rolled = copy.deepcopy(current)
                rolled["dice"] = list(action["dice"])
                append_decision(game, seat, "cube", "roll", "roll", current, rolled)
                current = rolled
            before = copy.deepcopy(current)
            terminal = (
                action_index == len(actions) - 1
                and game["terminal"]["kind"] != "resignation"
            )
            event = _fixture_terminal_event(game, engine_by_seat) if terminal else None
            subsequent = None
            if action_type == "checker":
                _apply_fixture_checker(current, seat, action["moves"])
                command = " ".join(f"{source}/{destination}" for source, destination in action["moves"])
                command_type = "checker"
                decision_type = "checker"
                analysis_dice = list(action["dice"])
                if not terminal:
                    current["on_roll"] = "X" if seat == "O" else "O"
                    current["decision"] = current["on_roll"]
                    current["dice"] = None
            elif action_type == "double":
                responder = "X" if seat == "O" else "O"
                current["pending"] = {"type": "double", "offerer": seat, "responder": responder}
                current["decision"] = responder
                command = command_type = "double"
                decision_type, analysis_dice = "cube", None
            elif action_type == "take":
                offerer = current["pending"]["offerer"]
                current["cube_value"] *= 2
                current["cube_owner"] = seat
                current["pending"] = {"type": "none"}
                current["on_roll"] = offerer
                current["decision"] = offerer
                current["dice"] = None
                command = command_type = "take"
                decision_type, analysis_dice = "cube", None
            elif action_type == "drop":
                command, command_type, decision_type, analysis_dice = "pass", "pass", "cube", None
            else:
                raise ValueError("unsupported connected fixture action")

            if terminal:
                current["score"] = list(game["post_score"])
                if game["game_number"] < summary["game_count"]:
                    next_game = summary["games"][game["game_number"]]
                    current = _next_opening_state(next_game, current["score"])
                    post_gnuid = _encode_fixture_gnuid(current)
                    subsequent = {
                        "game_number": game["game_number"] + 1, "gnuid": post_gnuid,
                        "score": list(current["score"]),
                        "on_roll_physical_seat": current["on_roll"],
                        "decision_physical_seat": current["decision"],
                        "dice": list(current["dice"]),
                    }
                else:
                    current["game_state"] = "game_over"
                    current["decision"] = None
                    current["dice"] = None
                    current["pending"] = {"type": "none"}
            append_decision(
                game, seat, decision_type, command, command_type, before, current,
                analysis_dice=analysis_dice, event=event, subsequent=subsequent,
            )

        if game["terminal"]["kind"] == "resignation":
            offerer = current["on_roll"]
            winner = game["winner_physical_seat"]
            if offerer == winner:
                raise ValueError("connected resignation fixture has the wrong offerer")
            offered = copy.deepcopy(current)
            offered["pending"] = {
                "type": "resignation", "offerer": offerer, "responder": winner,
                "multiplier": game["terminal"]["result_level"],
            }
            offered["decision"] = winner
            decisions[-1]["transition_evidence"]["automatic_transition"] = {
                "type": "resignation_offer",
                "from_gnuid": _encode_fixture_gnuid(current),
                "to_gnuid": _encode_fixture_gnuid(offered),
            }
            before = copy.deepcopy(offered)
            current = copy.deepcopy(offered)
            current["score"] = list(game["post_score"])
            subsequent = None
            if game["game_number"] < summary["game_count"]:
                next_game = summary["games"][game["game_number"]]
                current = _next_opening_state(next_game, current["score"])
                subsequent = {
                    "game_number": game["game_number"] + 1,
                    "gnuid": _encode_fixture_gnuid(current), "score": list(current["score"]),
                    "on_roll_physical_seat": current["on_roll"],
                    "decision_physical_seat": current["decision"], "dice": list(current["dice"]),
                }
            else:
                current["game_state"] = "resigned"
                current["decision"] = None
                current["dice"] = None
                current["pending"] = {"type": "none"}
            append_decision(
                game, winner, "board-rule", "accept", "accepted_resignation", before, current,
                event=_fixture_terminal_event(game, engine_by_seat), subsequent=subsequent,
            )

    (match / "decisions.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )

    fixture_action_ordinals: dict[int, int] = {}
    for decision in decisions:
        game_number = decision["game_number"]
        command_type = decision["transition_evidence"]["command_type"]
        if command_type in {"checker", "double", "take", "pass"}:
            fixture_action_ordinals[game_number] = fixture_action_ordinals.get(game_number, 0) + 1
            decision["action_ordinal"] = fixture_action_ordinals[game_number]
        else:
            decision["action_ordinal"] = None

    analysis_requests: list[dict[str, Any]] = []
    analysis_results: list[dict[str, Any]] = []
    for decision in decisions:
        decision_type = decision["decision_type"]
        if decision_type not in {"checker", "cube"}:
            continue
        request_ordinal = len(analysis_requests) + 1
        context = {
            "campaign_id": identity.campaign_id, "pair_id": identity.pair_id,
            "pair_index": identity.pair_index, "pair_member": side, "match_side": side,
            "game_number": decision["game_number"], "request_ordinal": request_ordinal,
            "decision_ordinal": decision["decision_ordinal"],
            "physical_seat": decision["physical_seat"], "engine": decision["engine"],
            "decision_type": decision_type, "gnuid": decision["gnuid"],
            "dice": decision["analysis_dice"],
        }
        target = FROZEN_CONFIG.data["engines"][decision["engine"]][
            f"{decision_type}_configured_target"
        ]
        configured_ply = int(target.removesuffix("ply"))
        actual_ply = (
            configured_ply - 1
            if decision["engine"] == "gnu" and decision_type == "checker"
            else configured_ply
        )
        if decision_type == "checker":
            decision_result: dict[str, Any] = {
                "actual_ply": actual_ply, "recommended_move_id": "fixture-move",
                "candidates": [{
                    "move_id": "fixture-move", "actual_ply": actual_ply,
                    "notation": decision["command"],
                }],
            }
            checker_decision, cube_decision = decision_result, None
            candidate_actuals: list[int] | None = [actual_ply]
        else:
            command = decision["command"]
            if command in {"take", "pass"}:
                take_equity, pass_equity = (
                    (0.2, 0.8) if command == "take" else (0.8, 0.2)
                )
                recommended = "double-take"
            else:
                take_equity, pass_equity = 0.2, 0.8
                recommended = "no-double" if command == "roll" else "double-take"
            decision_result = {
                "actual_ply": actual_ply,
                "recommendation": command,
                "recommended_action_id": recommended,
                "actions": [
                    {"action_id": "no-double", "equity": 0.1},
                    {"action_id": "double-take", "equity": take_equity},
                    {"action_id": "double-pass", "equity": pass_equity},
                ],
            }
            checker_decision, cube_decision = None, decision_result
            candidate_actuals = None
        raw_text = f"fixture raw result {side} {request_ordinal}"
        raw = {
            "position": {"id": decision["gnuid"], "format": "gnuid"},
            "engine": {"name": decision["engine"], "analysis_setting": target},
            "decision_type": decision_type, "status": "complete",
            "checker_decision": checker_decision, "cube_decision": cube_decision,
            "raw_source": {
                "inline": raw_text,
                "content_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            },
            "failure": None,
        }
        decision["engine_kit_result"] = {
            **raw,
            "campaign_depth_evidence": {
                "configured_target": target,
                "recommended_actual_ply": actual_ply,
                "candidate_actual_plies": candidate_actuals,
            },
        }
        analysis_requests.append(context)
        analysis_results.append({**context, "returned_result": raw})

    decision_path = match / "decisions.jsonl"
    decision_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )
    request_path = match / "analysis_requests.jsonl"
    result_path = match / "analysis_results.jsonl"
    request_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in analysis_requests),
        encoding="utf-8",
    )
    result_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in analysis_results),
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
            for game_number in range(1, 26)
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
        "analysis_request_evidence": "analysis_requests.jsonl",
        "analysis_result_evidence": "analysis_results.jsonl",
        "native_outputs": ["native/match.sgf", "native/match.txt"],
        "native_evidence": summary,
    }
    output_paths = [
        match / "native/match.sgf", match / "native/match.txt", decision_path,
        dice / "seat_dice_manifest.json", consumption, request_path, result_path,
    ]
    manifest["output_sha256"] = {
        str(path.relative_to(match)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_paths
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
