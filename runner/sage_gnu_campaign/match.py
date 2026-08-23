"""Two-human GNU board referee with decisions delegated to Engine Kit."""
from __future__ import annotations

import errno
import base64
import binascii
import json
import math
import os
import pty
import re
import shutil
import selectors
import subprocess
import termios
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from .config import CampaignConfig
from .dice import (
    SCHEMA_VERSION as DICE_SCHEMA_VERSION,
    SeatDiceController,
    dice_record,
    namespace_seed,
    stream_id,
    stream_sha256,
)
from .engine_kit import (
    EngineKitMismatch,
    EngineKitSession,
    analysis_result_forensics,
    validate_actual_depth_evidence,
)
from .identity import PairIdentity
from .manifests import fsync_directory, sha256_file, write_json


ANSI_RE = re.compile(r"\x1b\[[0-9?;]*[A-Za-z]")
POSITION_RE = re.compile(r"Position ID\s*:\s*(\S+)")
MATCH_RE = re.compile(r"Match ID\s*:\s*(\S+)")
PROMPT_RE = re.compile(rb"\x1b\[\?2004h[^\r\n]*\) $")
MANUAL_DICE = b"Enter dice:"
GNU_ERROR_RE = re.compile(
    r"(?im)^\s*(?:error:|unknown keyword\b|unknown command\b|illegal (?:move|play)\b|invalid (?:move|command)\b|you must set\b)"
)
TERMINAL_RESULT_RE = re.compile(
    r"(?im)^\s*(sage|gnu)_seat_([OX]) wins a "
    r"(single game|gammon|backgammon) and (\d+) points?\.\s*$"
)
TERMINAL_DROP_RE = re.compile(
    r"(?im)^\s*(sage|gnu)_seat_([OX]) refuses the cube and gives up (\d+) points?\.\s*$"
)
TERMINAL_RESIGN_RE = re.compile(
    r"(?im)^\s*(sage|gnu)_seat_([OX]) accepts and wins a "
    r"(single game|gammon|backgammon)\.\s*$"
)
TEXT_MATCH_RE = re.compile(r"(?im)\b(?:\d+\s+point\s+match|match\s+to\s+\d+\s+points?)\b")
TEXT_GAME_RE = re.compile(r"(?im)^\s*game\s+(\d+)\b")
TEXT_PLAYER_SCORE_RE = re.compile(
    r"(?im)\b([A-Za-z][A-Za-z0-9_-]*)_seat_([OX])\s*:\s*(\d+)\b"
)
TEXT_IDENTITY_RE = re.compile(r"(?im)\b[A-Za-z][A-Za-z0-9_-]*_seat_[OX]\s*:")
TEXT_RESULT_RE = re.compile(r"(?i)\bwins\s+(\d+)\s+points?\b")
TEXT_ACTION_RE = re.compile(
    r"(?i)(?P<checker>[1-6]{2}):\s*(?P<move>.*?)"
    r"(?=(?:\s{2,}(?:[1-6]{2}:|Doubles\s*=>|Takes\b|Drops\b|Wins\b))|$)"
    r"|(?P<double>Doubles)\s*=>\s*(?P<cube>\d+)"
    r"|(?P<take>Takes)\b|(?P<drop>Drops)\b"
)
SGF_RESULT_RE = re.compile(r"^([WB])\+(\d+)(R(?:esign)?)?$", re.IGNORECASE)
GNU_RUNTIME_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+) (\d{8})$")
GNU_POSITION_ID_RE = re.compile(r"^[A-Za-z0-9+/]{14}$")
GNU_MATCH_ID_RE = re.compile(r"^[A-Za-z0-9+/]{12}$")
NO_RETURNED_RESULT = object()


def _frozen_gnu_sgf_application(config: CampaignConfig) -> str:
    """Derive GNU's SGF AP identity from the verified frozen runtime identity."""
    runtime_version = config.data.get("engines", {}).get("gnu", {}).get(
        "runtime_identity", {}
    ).get("engine_version")
    if not isinstance(runtime_version, str):
        raise MatchExecutionError("frozen GNU runtime version identity is missing or malformed")
    match = GNU_RUNTIME_VERSION_RE.fullmatch(runtime_version)
    if match is None:
        raise MatchExecutionError("frozen GNU runtime version identity is missing or malformed")
    return f"GNU Backgammon:{match.group(1)}"


def _decode_gnu_id_component(
    value: str, pattern: re.Pattern[str], expected_bytes: int, label: str,
) -> bytes:
    if pattern.fullmatch(value) is None:
        raise MatchExecutionError(f"publication {label} has invalid Base64 spelling")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MatchExecutionError(f"publication {label} is invalid Base64") from exc
    if len(decoded) != expected_bytes:
        raise MatchExecutionError(f"publication {label} has invalid decoded length")
    return decoded


def _little_endian_bits(data: bytes) -> tuple[int, ...]:
    return tuple((byte >> bit) & 1 for byte in data for bit in range(8))


def _bit_value(bits: tuple[int, ...], start: int, width: int) -> int:
    return sum(bits[start + offset] << offset for offset in range(width))


def _decode_publication_gnuid(value: str) -> Any:
    """Strictly decode the state fields used by live command-transition policy."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise MatchExecutionError("publication GNUID must contain one Position ID and Match ID")
    position_id, match_id = value.split(":", 1)
    position_bits = _little_endian_bits(
        _decode_gnu_id_component(position_id, GNU_POSITION_ID_RE, 10, "Position ID")
    )
    cursor = 0
    blocks: list[list[int]] = []
    for _player_index in range(2):
        points: list[int] = []
        for _point_index in range(25):
            count = 0
            while cursor < len(position_bits) and position_bits[cursor] == 1:
                count += 1
                cursor += 1
            if cursor >= len(position_bits):
                raise MatchExecutionError("publication Position ID ends inside a checker count")
            cursor += 1
            points.append(count)
        if sum(points) > 15:
            raise MatchExecutionError("publication Position ID exceeds fifteen checkers")
        blocks.append(points)
    if any(position_bits[cursor:]):
        raise MatchExecutionError("publication Position ID has noncanonical padding")

    bits = _little_endian_bits(
        _decode_gnu_id_component(match_id, GNU_MATCH_ID_RE, 9, "Match ID")
    )
    cube_exp = _bit_value(bits, 0, 4)
    cube_owner_code = _bit_value(bits, 4, 2)
    on_roll_index = _bit_value(bits, 6, 1)
    game_state_code = _bit_value(bits, 8, 3)
    decision_index = _bit_value(bits, 11, 1)
    doubled = bool(_bit_value(bits, 12, 1))
    resignation = _bit_value(bits, 13, 2)
    die1 = _bit_value(bits, 15, 3)
    die2 = _bit_value(bits, 18, 3)
    match_length = _bit_value(bits, 21, 15)
    score0 = _bit_value(bits, 36, 15)
    score1 = _bit_value(bits, 51, 15)
    if tuple(bits[66:]) != (1, 0, 0, 0, 0, 0):
        raise MatchExecutionError("publication Match ID has noncanonical framing")
    if cube_owner_code == 2 or cube_exp > 10:
        raise MatchExecutionError("publication Match ID has invalid cube state")
    if (die1 == 0) != (die2 == 0) or die1 > 6 or die2 > 6:
        raise MatchExecutionError("publication Match ID has invalid dice")
    if doubled and resignation:
        raise MatchExecutionError("publication Match ID has conflicting pending actions")
    game_state = {0: "setup", 1: "playing", 2: "game_over", 3: "resigned", 4: "game_over"}.get(
        game_state_code
    )
    if game_state is None:
        raise MatchExecutionError("publication Match ID has an unsupported game state")

    on_roll = f"player_{on_roll_index}"
    decision_player: str | None = f"player_{decision_index}"
    first, second = blocks
    encoded_by_player = (second, first) if on_roll_index == 0 else (first, second)

    def player_board(encoded: list[int]) -> Any:
        represented = sum(encoded)
        return SimpleNamespace(points=tuple(encoded[:24]), bar=encoded[24], off=15 - represented)

    pending = SimpleNamespace(
        type="none", offerer=None, responder=None,
        offered_cube_value=None, resignation_multiplier=None,
    )
    cube_value = 2 ** cube_exp
    if doubled:
        pending = SimpleNamespace(
            type="double",
            offerer=f"player_{1 - decision_index}", responder=f"player_{decision_index}",
            offered_cube_value=cube_value * 2, resignation_multiplier=None,
        )
    elif resignation:
        pending = SimpleNamespace(
            type="resignation",
            offerer=f"player_{1 - decision_index}", responder=f"player_{decision_index}",
            offered_cube_value=None, resignation_multiplier=resignation,
        )
    if game_state != "playing":
        decision_player = None
    board0, board1 = (player_board(encoded_by_player[0]), player_board(encoded_by_player[1]))
    return SimpleNamespace(
        board=SimpleNamespace(
            checker_count=SimpleNamespace(player_0=15, player_1=15),
            player_0=board0, player_1=board1,
        ),
        state=SimpleNamespace(
            game_state=game_state, on_roll=on_roll, decision_player=decision_player,
            dice=None if die1 == 0 else (die1, die2),
        ),
        cube=SimpleNamespace(
            value=cube_value,
            owner={0: "player_0", 1: "player_1", 3: "center"}[cube_owner_code],
            pending_action=pending,
        ),
        score=SimpleNamespace(player_0=score0, player_1=score1, match_length=match_length),
    )


def _parse_terminal_event(output: str) -> dict[str, Any] | None:
    """Retain exact GNU terminal semantics before automatic game advancement."""
    results = TERMINAL_RESULT_RE.findall(output)
    drops = TERMINAL_DROP_RE.findall(output)
    resignations = TERMINAL_RESIGN_RE.findall(output)
    if not results:
        if drops or resignations:
            raise MatchExecutionError("GNU terminal output has action semantics without one game result")
        return None
    if len(results) != 1 or len(drops) > 1 or len(resignations) > 1 or (drops and resignations):
        raise MatchExecutionError("GNU terminal output is ambiguous or duplicated")
    winner_engine, winner_seat, result_name, raw_points = results[0]
    points = int(raw_points)
    if points <= 0:
        raise MatchExecutionError("GNU terminal output has invalid awarded points")
    event: dict[str, Any] = {
        "kind": "ordinary_game_over",
        "winner_physical_seat": winner_seat,
        "winner_engine": winner_engine,
        "points": points,
        "result_level": {"single game": 1, "gammon": 2, "backgammon": 3}[result_name.lower()],
    }
    if drops:
        loser_engine, loser_seat, raw_given_points = drops[0]
        if loser_seat == winner_seat or int(raw_given_points) != points:
            raise MatchExecutionError("GNU drop output conflicts with its game result")
        event.update({
            "kind": "drop",
            "loser_physical_seat": loser_seat,
            "loser_engine": loser_engine,
        })
    elif resignations:
        resign_winner_engine, resign_winner_seat, resign_result_name = resignations[0]
        resignation_level = {"single game": 1, "gammon": 2, "backgammon": 3}[
            resign_result_name.lower()
        ]
        if (resign_winner_engine, resign_winner_seat) != (winner_engine, winner_seat):
            raise MatchExecutionError("GNU resignation output conflicts with its game result")
        event.update({"kind": "resignation", "resignation_level": resignation_level})
    return event


def _raise_on_gnu_error(command: str, output: str) -> None:
    match = GNU_ERROR_RE.search(output)
    if match is not None:
        line = output[match.start():].splitlines()[0].strip()
        raise MatchExecutionError(f"GNU rejected {command!r}: {line[:500]}")


def _append_jsonl_durable(path: Path, record: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _create_empty_file_durable(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)


def _durably_create_directory_hierarchy(anchor: Path, target: Path) -> None:
    """Create each directory below an already durable anchor, leaf before parent."""
    durable_anchor = Path(anchor).resolve(strict=True)
    requested_target = Path(target).resolve(strict=False)
    if not durable_anchor.is_dir() or not requested_target.is_relative_to(durable_anchor):
        raise MatchExecutionError("journal hierarchy is not below its durable directory anchor")
    current = durable_anchor
    for component in requested_target.relative_to(durable_anchor).parts:
        candidate = current / component
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_dir():
                raise MatchExecutionError(f"journal hierarchy conflicts with non-directory: {candidate}")
        else:
            candidate.mkdir()
            fsync_directory(candidate)
            fsync_directory(current)
        current = candidate


def _durably_link_existing_directory_hierarchy(anchor: Path, target: Path) -> None:
    """Flush an existing leaf-to-anchor chain so every directory entry is linked."""
    durable_anchor = Path(anchor).resolve(strict=True)
    requested_target = Path(target).resolve(strict=True)
    if not durable_anchor.is_dir() or not requested_target.is_relative_to(durable_anchor):
        raise MatchExecutionError("existing journal hierarchy is not below its durable anchor")
    relative = requested_target.relative_to(durable_anchor)
    directories = [
        durable_anchor.joinpath(*relative.parts[:index])
        for index in range(1, len(relative.parts) + 1)
    ]
    if any(path.is_symlink() or not path.is_dir() for path in directories):
        raise MatchExecutionError("existing journal hierarchy contains a non-directory component")
    for directory in reversed(directories):
        fsync_directory(directory)
    fsync_directory(durable_anchor)


def _validate_native_outputs(
    sgf_path: Path,
    text_path: Path,
    expected_engine_by_seat: Mapping[str, str],
    expected_sgf_application: str,
) -> dict[str, Any]:
    try:
        sgf = sgf_path.read_text(encoding="utf-8-sig")
        exported = text_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise MatchExecutionError("GNU native outputs are absent or unreadable") from exc
    if (
        set(expected_engine_by_seat) != {"O", "X"}
        or set(expected_engine_by_seat.values()) != {"sage", "gnu"}
    ):
        raise MatchExecutionError("GNU native player authority is invalid")
    sgf_games = _parse_sgf_match(
        sgf.strip(), expected_engine_by_seat, expected_sgf_application
    )
    text_games = _parse_text_match(exported, expected_engine_by_seat)
    if len(sgf_games) != len(text_games):
        raise MatchExecutionError("GNU SGF/text game collections or results do not match")
    for sgf_game, text_game in zip(sgf_games, text_games):
        text_terminal = text_game["terminal"]
        sgf_terminal = sgf_game["terminal"]
        shared_text_terminal = {
            key: value for key, value in text_terminal.items() if key != "kind"
        }
        shared_sgf_terminal = {
            key: value for key, value in sgf_terminal.items()
            if key not in {"kind", "resignation_recorded"}
        }
        if (
            {key: value for key, value in sgf_game.items() if key != "terminal"}
            != {key: value for key, value in text_game.items() if key != "terminal"}
            or shared_sgf_terminal != shared_text_terminal
            or (sgf_terminal["kind"] == "drop") != (text_terminal["kind"] == "drop")
        ):
            raise MatchExecutionError("GNU SGF/text complete ordered game semantics do not match")
    return {"game_count": len(sgf_games), "games": sgf_games, "final_score": sgf_games[-1]["post_score"]}


def _split_sgf_collection(value: str) -> list[str]:
    if len(value) < 24 or "\x00" in value:
        raise MatchExecutionError("GNU saved SGF is empty or structurally invalid")
    trees: list[str] = []
    depth = 0
    start: int | None = None
    in_property = False
    escaped = False
    for index, character in enumerate(value):
        if in_property:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "]":
                in_property = False
            continue
        if character == "[":
            in_property = True
        elif character == "(":
            if depth == 0:
                start = index
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise MatchExecutionError("GNU saved SGF is empty or structurally invalid")
            if depth == 0:
                if start is None:
                    raise MatchExecutionError("GNU saved SGF is empty or structurally invalid")
                trees.append(value[start:index + 1])
                start = None
        elif depth == 0 and not character.isspace():
            raise MatchExecutionError("GNU saved SGF has data outside game trees")
    if depth or in_property or escaped or start is not None or not trees:
        raise MatchExecutionError("GNU saved SGF is empty or structurally invalid")
    return trees


def _sgf_root_properties(tree: str) -> dict[str, list[str]]:
    return _sgf_linear_nodes(tree)[0]


def _sgf_linear_nodes(tree: str) -> list[dict[str, list[str]]]:
    """Parse one variation-free GNU game tree without discarding move nodes."""
    if not tree.startswith("(") or not tree.endswith(")"):
        raise MatchExecutionError("GNU saved SGF game tree is malformed")
    index = 1
    limit = len(tree) - 1
    nodes: list[dict[str, list[str]]] = []
    while index < limit:
        while index < limit and tree[index].isspace():
            index += 1
        if index >= limit:
            break
        if tree[index] != ";":
            raise MatchExecutionError("GNU saved SGF contains a variation or malformed node sequence")
        index += 1
        properties: dict[str, list[str]] = {}
        while index < limit:
            while index < limit and tree[index].isspace():
                index += 1
            if index >= limit or tree[index] == ";":
                break
            if tree[index] in "()":
                raise MatchExecutionError("GNU saved SGF contains a variation or malformed node sequence")
            name_start = index
            while index < limit and "A" <= tree[index] <= "Z":
                index += 1
            name = tree[name_start:index]
            if not name or index >= limit or tree[index] != "[":
                raise MatchExecutionError("GNU saved SGF property is malformed")
            values: list[str] = []
            while index < limit and tree[index] == "[":
                index += 1
                characters: list[str] = []
                while index < limit:
                    character = tree[index]
                    index += 1
                    if character == "\\":
                        if index >= limit:
                            raise MatchExecutionError("GNU saved SGF property escape is truncated")
                        escaped = tree[index]
                        index += 1
                        if escaped in "\r\n":
                            continue
                        characters.append(escaped)
                    elif character == "]":
                        break
                    else:
                        characters.append(character)
                else:
                    raise MatchExecutionError("GNU saved SGF property is truncated")
                values.append("".join(characters))
            if name in properties:
                raise MatchExecutionError(f"GNU saved SGF duplicates node property {name}")
            properties[name] = values
        if not properties:
            raise MatchExecutionError("GNU saved SGF contains an empty node")
        nodes.append(properties)
    if not nodes:
        raise MatchExecutionError("GNU saved SGF game tree lacks a root node")
    return nodes


def _one_sgf_property(properties: Mapping[str, list[str]], name: str) -> str:
    values = properties.get(name)
    if values is None or len(values) != 1:
        raise MatchExecutionError(f"GNU saved SGF requires exactly one {name} property")
    return values[0]


def _sgf_point(value: str, seat: str) -> str:
    if value == "y":
        return "bar"
    if value == "z":
        return "off"
    if len(value) != 1 or not "a" <= value <= "x":
        raise MatchExecutionError("GNU saved SGF checker move has an invalid point")
    offset = ord(value) - ord("a")
    return str(offset + 1 if seat == "O" else 24 - offset)


def _parse_sgf_actions(nodes: list[dict[str, list[str]]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if len(nodes) < 2:
        raise MatchExecutionError("GNU saved SGF game has no played action body")
    for node in nodes[1:]:
        move_properties = [name for name in ("W", "B") if name in node]
        if len(move_properties) != 1:
            raise MatchExecutionError("GNU saved SGF action node lacks one exact player move")
        property_name = move_properties[0]
        raw = _one_sgf_property(node, property_name)
        seat = "O" if property_name == "W" else "X"
        lowered = raw.lower()
        if lowered in {"double", "take", "drop"}:
            actions.append({"action": lowered, "physical_seat": seat})
            continue
        if len(raw) < 2 or not raw[:2].isdigit() or any(character not in "123456" for character in raw[:2]):
            raise MatchExecutionError("GNU saved SGF checker action lacks exact dice")
        encoded_moves = raw[2:]
        if len(encoded_moves) % 2 or len(encoded_moves) > 8:
            raise MatchExecutionError("GNU saved SGF checker action has malformed ordered moves")
        moves = [
            [_sgf_point(encoded_moves[offset], seat), _sgf_point(encoded_moves[offset + 1], seat)]
            for offset in range(0, len(encoded_moves), 2)
        ]
        actions.append({
            "action": "checker",
            "physical_seat": seat,
            "dice": [int(raw[0]), int(raw[1])],
            "moves": moves,
        })
    return actions


def _parse_text_moves(value: str) -> list[list[str]]:
    rendered = value.strip()
    if rendered.lower() == "cannot move":
        return []
    if not rendered:
        raise MatchExecutionError("GNU exported match text checker action lacks ordered moves")
    moves: list[list[str]] = []
    for token in rendered.split():
        repetition = 1
        repeated = re.fullmatch(r"(.+?)\((\d+)\)", token)
        if repeated is not None:
            token = repeated.group(1)
            repetition = int(repeated.group(2))
            if not 2 <= repetition <= 4:
                raise MatchExecutionError("GNU exported match text has an invalid move repetition")
        components = [part.rstrip("*").lower() for part in token.split("/")]
        if len(components) < 2 or any(
            part not in {"bar", "off"} and (not part.isdigit() or not 1 <= int(part) <= 24)
            for part in components
        ):
            raise MatchExecutionError("GNU exported match text has malformed checker notation")
        expanded = [[source, destination] for source, destination in zip(components, components[1:])]
        moves.extend(expanded * repetition)
    if len(moves) > 4:
        raise MatchExecutionError("GNU exported match text checker action has too many ordered moves")
    return moves


def _validate_action_structure(
    actions: list[dict[str, Any]], winner_seat: str, points: int, terminal_kind: str,
) -> tuple[list[dict[str, Any]], int]:
    """Enrich and validate the linear normal-match action/cube state machine."""
    if not actions or actions[0].get("action") != "checker":
        raise MatchExecutionError("GNU native game lacks a checker-play opening action")
    if actions[0].get("dice", [0, 0])[0] == actions[0].get("dice", [0, 0])[1]:
        raise MatchExecutionError("GNU native game opening action uses tied dice")
    expected = actions[0]["physical_seat"]
    cube_value = 1
    cube_owner = "center"
    pending_doubler: str | None = None
    enriched: list[dict[str, Any]] = []
    for ordinal, action in enumerate(actions, 1):
        kind = action.get("action")
        seat = action.get("physical_seat")
        if seat not in {"O", "X"} or seat != expected:
            raise MatchExecutionError("GNU native game action actor/order is invalid")
        event = {**action, "action_ordinal": ordinal, "cube_value_before": cube_value}
        if kind == "checker":
            if pending_doubler is not None:
                raise MatchExecutionError("GNU native checker action precedes a cube response")
            dice = action.get("dice")
            moves = action.get("moves")
            if (
                not isinstance(dice, list) or len(dice) != 2
                or any(type(die) is not int or not 1 <= die <= 6 for die in dice)
                or not isinstance(moves, list)
            ):
                raise MatchExecutionError("GNU native checker action is malformed")
            expected = "X" if seat == "O" else "O"
        elif kind == "double":
            if pending_doubler is not None or cube_owner not in {"center", seat}:
                raise MatchExecutionError("GNU native double action has invalid cube ownership")
            recorded_offer = event.pop("recorded_cube_value", None)
            if recorded_offer is not None and recorded_offer != cube_value * 2:
                raise MatchExecutionError("GNU exported match text records the wrong offered cube value")
            pending_doubler = seat
            expected = "X" if seat == "O" else "O"
            event["cube_value_offered"] = cube_value * 2
        elif kind == "take":
            if pending_doubler is None or seat == pending_doubler:
                raise MatchExecutionError("GNU native take action lacks the matching double")
            cube_value *= 2
            cube_owner = seat
            expected = pending_doubler
            pending_doubler = None
            event["cube_value_after"] = cube_value
            event["cube_owner_after"] = cube_owner
        elif kind == "drop":
            if pending_doubler is None or seat == pending_doubler:
                raise MatchExecutionError("GNU native drop action lacks the matching double")
            event["cube_value_declined"] = cube_value * 2
            pending_doubler = None
        else:
            raise MatchExecutionError("GNU native game contains an unsupported action")
        enriched.append(event)
    if pending_doubler is not None:
        raise MatchExecutionError("GNU native game ends with an unanswered double")
    last = enriched[-1]
    if terminal_kind == "drop":
        if last["action"] != "drop" or last["physical_seat"] == winner_seat or points != cube_value:
            raise MatchExecutionError("GNU native drop terminal conflicts with action/cube history")
        result_level = 1
    elif terminal_kind in {"ordinary_game_over", "resignation", "unspecified_completion"}:
        if last["action"] == "drop" or terminal_kind == "ordinary_game_over" and (
            last["action"] != "checker" or last["physical_seat"] != winner_seat
        ):
            raise MatchExecutionError("GNU native completion conflicts with its terminal action")
        if points % cube_value or not 1 <= points // cube_value <= 3:
            raise MatchExecutionError("GNU native completion points conflict with cube history")
        result_level = points // cube_value
    else:
        raise MatchExecutionError("GNU native game has an unknown terminal kind")
    return enriched, result_level


def _parse_sgf_match(
    value: str,
    expected_engine_by_seat: Mapping[str, str],
    expected_sgf_application: str,
) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    score = [0, 0]
    expected_players = {
        "PW": f"{expected_engine_by_seat['O']}_seat_O",
        "PB": f"{expected_engine_by_seat['X']}_seat_X",
    }
    for index, tree in enumerate(_split_sgf_collection(value)):
        nodes = _sgf_linear_nodes(tree)
        properties = nodes[0]
        if (
            _one_sgf_property(properties, "FF") != "4"
            or _one_sgf_property(properties, "GM") != "6"
            or _one_sgf_property(properties, "AP") != expected_sgf_application
            or any(_one_sgf_property(properties, name) != player for name, player in expected_players.items())
        ):
            raise MatchExecutionError("GNU saved SGF has invalid format or player identities")
        mi_values = properties.get("MI")
        if not mi_values:
            raise MatchExecutionError("GNU saved SGF lacks complete match information")
        match_info: dict[str, int] = {}
        for item in mi_values:
            tag, separator, raw = item.partition(":")
            if separator != ":" or tag.lower() in match_info or not raw.isdigit():
                raise MatchExecutionError("GNU saved SGF match information is malformed")
            match_info[tag.lower()] = int(raw)
        if (
            match_info != {"length": 7, "game": index, "ws": score[0], "bs": score[1]}
            or max(score) >= 7
        ):
            raise MatchExecutionError("GNU saved SGF game order or score progression is invalid")
        result_match = SGF_RESULT_RE.fullmatch(_one_sgf_property(properties, "RE"))
        if result_match is None:
            raise MatchExecutionError("GNU saved SGF game result is missing or malformed")
        winner_seat = "O" if result_match.group(1).upper() == "W" else "X"
        points = int(result_match.group(2))
        if points <= 0:
            raise MatchExecutionError("GNU saved SGF game result has invalid points")
        actions = _parse_sgf_actions(nodes)
        terminal_kind = (
            "resignation" if result_match.group(3) is not None
            else "drop" if actions[-1]["action"] == "drop"
            else "ordinary_game_over"
        )
        actions, result_level = _validate_action_structure(
            actions, winner_seat, points, terminal_kind
        )
        start_score = list(score)
        score[0 if winner_seat == "O" else 1] += points
        games.append({
            "game_number": index + 1,
            "players_by_physical_seat": dict(expected_engine_by_seat),
            "start_score": start_score,
            "opening_state": {
                "cube_value": 1,
                "on_roll_physical_seat": actions[0]["physical_seat"],
                "dice": actions[0]["dice"],
            },
            "actions": actions,
            "terminal": {
                "kind": terminal_kind,
                "winner_physical_seat": winner_seat,
                "winner_engine": expected_engine_by_seat[winner_seat],
                "points": points,
                "result_level": result_level,
                "resignation_recorded": terminal_kind == "resignation",
            },
            "winner_physical_seat": winner_seat,
            "winner_engine": expected_engine_by_seat[winner_seat],
            "points": points,
            "post_score": list(score),
        })
    if max(score) < 7:
        raise MatchExecutionError("GNU saved SGF does not contain a complete seven-point match")
    return games


def _text_action_actor(column: int, player_columns: Mapping[str, int]) -> str:
    distances = {seat: abs(column - player_columns[seat]) for seat in ("O", "X")}
    if distances["O"] == distances["X"]:
        raise MatchExecutionError("GNU exported match text action column is ambiguous")
    return min(distances, key=distances.get)


def _parse_text_actions(block: str, player_columns: Mapping[str, int]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for line in block.splitlines():
        for match in TEXT_ACTION_RE.finditer(line):
            seat = _text_action_actor(match.start(), player_columns)
            if match.group("checker") is not None:
                move_text = match.group("move").strip()
                # A dice-only MOVE_SETDICE record can precede resignation and is
                # stronger text-only evidence, but it is not a played SGF move.
                if not move_text:
                    continue
                actions.append({
                    "action": "checker",
                    "physical_seat": seat,
                    "dice": [int(match.group("checker")[0]), int(match.group("checker")[1])],
                    "moves": _parse_text_moves(move_text),
                })
            elif match.group("double") is not None:
                actions.append({
                    "action": "double", "physical_seat": seat,
                    "recorded_cube_value": int(match.group("cube")),
                })
            elif match.group("take") is not None:
                actions.append({"action": "take", "physical_seat": seat})
            elif match.group("drop") is not None:
                actions.append({"action": "drop", "physical_seat": seat})
    return actions


def _parse_text_match(value: str, expected_engine_by_seat: Mapping[str, str]) -> list[dict[str, Any]]:
    match_headers = list(TEXT_MATCH_RE.finditer(value))
    game_headers = list(TEXT_GAME_RE.finditer(value))
    if (
        len(value.strip()) < 24
        or "\x00" in value
        or len(match_headers) != 1
        or int(re.search(r"\d+", match_headers[0].group(0)).group(0)) != 7
        or not game_headers
        or [int(header.group(1)) for header in game_headers] != list(range(1, len(game_headers) + 1))
    ):
        raise MatchExecutionError("GNU exported match text is empty or structurally invalid")
    expected_players = sorted((engine, seat) for seat, engine in expected_engine_by_seat.items())
    score = [0, 0]
    games: list[dict[str, Any]] = []
    accepted_identity_spans: list[tuple[int, int]] = []
    for index, header in enumerate(game_headers):
        block_end = game_headers[index + 1].start() if index + 1 < len(game_headers) else len(value)
        block = value[header.end():block_end]
        player_lines: list[tuple[int, str, list[re.Match[str]]]] = []
        offset = header.end()
        for line in block.splitlines(keepends=True):
            matches = list(TEXT_PLAYER_SCORE_RE.finditer(line))
            if matches:
                player_lines.append((offset, line, matches))
            offset += len(line)
        if len(player_lines) != 1:
            raise MatchExecutionError("GNU exported match text lacks one exact player/score line per game")
        line_offset, _, player_matches = player_lines[0]
        if sorted((match.group(1), match.group(2)) for match in player_matches) != expected_players:
            raise MatchExecutionError("GNU exported match text has invalid per-game player identities")
        if len(player_matches) != 2:
            raise MatchExecutionError("GNU exported match text duplicates per-game player identities")
        by_seat = {match.group(2): match for match in player_matches}
        observed_score = [int(by_seat["O"].group(3)), int(by_seat["X"].group(3))]
        if observed_score != score or max(score) >= 7:
            raise MatchExecutionError("GNU exported match text score progression is invalid")
        accepted_identity_spans.extend(
            (line_offset + match.start(), line_offset + match.end()) for match in player_matches
        )
        result_matches = list(TEXT_RESULT_RE.finditer(block))
        if len(result_matches) != 1:
            raise MatchExecutionError("GNU exported match text lacks one exact result per game")
        result = result_matches[0]
        points = int(result.group(1))
        if points <= 0:
            raise MatchExecutionError("GNU exported match text has invalid result points")
        result_line_start = block.rfind("\n", 0, result.start()) + 1
        result_column = len(block[result_line_start:result.start()].expandtabs())
        player_columns = {seat: by_seat[seat].start() for seat in ("O", "X")}
        distances = {seat: abs(column - result_column) for seat, column in player_columns.items()}
        if (
            distances["O"] == distances["X"]
            or header.end() + result.start() <= line_offset
        ):
            raise MatchExecutionError("GNU exported match text result column or ordering is ambiguous")
        winner_seat = min(distances, key=distances.get)
        actions = _parse_text_actions(block, player_columns)
        terminal_kind = "drop" if actions and actions[-1]["action"] == "drop" else "unspecified_completion"
        actions, result_level = _validate_action_structure(
            actions, winner_seat, points, terminal_kind
        )
        start_score = list(score)
        score[0 if winner_seat == "O" else 1] += points
        games.append({
            "game_number": index + 1,
            "players_by_physical_seat": dict(expected_engine_by_seat),
            "start_score": start_score,
            "opening_state": {
                "cube_value": 1,
                "on_roll_physical_seat": actions[0]["physical_seat"],
                "dice": actions[0]["dice"],
            },
            "actions": actions,
            "terminal": {
                "kind": terminal_kind,
                "winner_physical_seat": winner_seat,
                "winner_engine": expected_engine_by_seat[winner_seat],
                "points": points,
                "result_level": result_level,
            },
            "winner_physical_seat": winner_seat,
            "winner_engine": expected_engine_by_seat[winner_seat],
            "points": points,
            "post_score": list(score),
        })
    all_identity_spans = [(match.start(), match.end()) for match in TEXT_IDENTITY_RE.finditer(value)]
    if len(all_identity_spans) != len(accepted_identity_spans) or any(
        not any(start == accepted_start for accepted_start, _ in accepted_identity_spans)
        for start, _ in all_identity_spans
    ):
        raise MatchExecutionError("GNU exported match text has player identities outside valid game blocks")
    if max(score) < 7:
        raise MatchExecutionError("GNU exported match text does not contain a complete seven-point match")
    return games


def _read_jsonl_evidence(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatchExecutionError(f"{label} is absent or malformed") from exc
    if not records or any(not isinstance(record, dict) for record in records):
        raise MatchExecutionError(f"{label} is absent or malformed")
    return records


def _validate_complete_native_evidence(
    match_root: Path,
    expected_engine_by_seat: Mapping[str, str],
    expected_sgf_application: str,
    *,
    identity: PairIdentity | None = None,
    match_side: str | None = None,
    roll_count: int | None = None,
    files_per_match: int | None = None,
    configured_targets: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Reconcile the complete native match against every numbered journal."""
    match_root = Path(match_root)
    native = match_root / "native"
    summary = _validate_native_outputs(
        native / "match.sgf", native / "match.txt", expected_engine_by_seat,
        expected_sgf_application,
    )
    expected_numbers = list(range(1, summary["game_count"] + 1))
    try:
        manifest = json.loads((match_root / "match_manifest.json").read_text(encoding="utf-8"))
        dice_manifest = json.loads((match_root / "dice/seat_dice_manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatchExecutionError("match or deterministic-dice manifest is absent or malformed") from exc
    if (
        manifest.get("engine_by_physical_seat") != dict(expected_engine_by_seat)
        or manifest.get("native_evidence") != summary
        or manifest.get("native_outputs") != ["native/match.sgf", "native/match.txt"]
        or manifest.get("candidate_actual_depth_evidence") != "decisions.jsonl"
        or manifest.get("dice_manifest") != "dice/seat_dice_manifest.json"
        or manifest.get("dice_consumption") != "dice/seat_dice_consumption.jsonl"
    ):
        raise MatchExecutionError("match manifest does not reconcile with complete native evidence")

    decisions = _read_jsonl_evidence(match_root / "decisions.jsonl", "decision journal")
    for record in decisions:
        seat = record.get("physical_seat")
        if (
            type(record.get("game_number")) is not int
            or seat not in {"O", "X"}
            or record.get("engine") != expected_engine_by_seat[seat]
        ):
            raise MatchExecutionError("decision journal has invalid game/seat/engine evidence")
    decision_numbers = sorted({record["game_number"] for record in decisions})
    if decision_numbers != expected_numbers:
        raise MatchExecutionError("decision journal game numbering does not match native games")
    for game in summary["games"]:
        terminals = []
        for record in decisions:
            transition = record.get("transition_evidence")
            if record.get("game_number") == game["game_number"] and isinstance(transition, dict):
                event = transition.get("terminal_event")
                if event is not None:
                    if not isinstance(event, dict):
                        raise MatchExecutionError("decision terminal evidence is malformed")
                    terminals.append((transition, event))
        if len(terminals) != 1:
            raise MatchExecutionError("decision journal lacks one exact terminal event per native game")
        transition, event = terminals[0]
        if (
            transition.get("game_number") != game["game_number"]
            or event.get("winner_physical_seat") != game["winner_physical_seat"]
            or event.get("winner_engine") != game["winner_engine"]
            or event.get("points") != game["points"]
            or not isinstance(transition.get("post_command"), dict)
            or transition["post_command"].get("score") != game["post_score"]
        ):
            raise MatchExecutionError("decision terminal evidence conflicts with native game result")

    consumption_path = match_root / "dice/seat_dice_consumption.jsonl"
    consumption = _read_jsonl_evidence(consumption_path, "deterministic dice consumption journal")
    for record in consumption:
        seat = record.get("physical_seat")
        if (
            type(record.get("game_number")) is not int
            or seat not in {"O", "X"}
            or record.get("engine") != expected_engine_by_seat[seat]
        ):
            raise MatchExecutionError("deterministic dice journal has invalid game/seat/engine evidence")
    dice_numbers = sorted({record["game_number"] for record in consumption})
    if dice_numbers != expected_numbers:
        raise MatchExecutionError("deterministic dice journal game numbering does not match native games")
    for game_number in expected_numbers:
        openings = [
            record for record in consumption
            if record.get("game_number") == game_number and record.get("prompt_type") == "opening"
        ]
        if {record.get("physical_seat") for record in openings} != {"O", "X"}:
            raise MatchExecutionError("deterministic dice journal lacks both opening-seat streams per game")
    consumption_authority = dice_manifest.get("consumption")
    streams = dice_manifest.get("streams")
    if (
        dice_manifest.get("engine_by_physical_seat") != dict(expected_engine_by_seat)
        or not isinstance(consumption_authority, dict)
        or consumption_authority.get("path") != consumption_path.name
        or consumption_authority.get("entries") != len(consumption)
        or consumption_authority.get("sha256") != sha256_file(consumption_path)
        or not isinstance(streams, list)
    ):
        raise MatchExecutionError("deterministic dice manifest does not reconcile with consumption evidence")
    if any(
        sum(
            isinstance(stream, dict)
            and stream.get("game_number") == game_number
            and stream.get("physical_seat") == seat
            and stream.get("engine") == expected_engine_by_seat[seat]
            for stream in streams
        ) != 1
        for game_number in expected_numbers for seat in ("O", "X")
    ):
        raise MatchExecutionError("deterministic dice manifest lacks native-game seat streams")
    authority = (identity, match_side, roll_count, files_per_match, configured_targets)
    if any(value is not None for value in authority):
        if any(value is None for value in authority):
            raise MatchExecutionError("publication dice authority is incomplete")
        assert identity is not None and match_side is not None
        assert roll_count is not None and files_per_match is not None
        assert configured_targets is not None
        requests = _read_jsonl_evidence(
            match_root / "analysis_requests.jsonl", "analysis request journal"
        )
        results = _read_jsonl_evidence(
            match_root / "analysis_results.jsonl", "analysis result journal"
        )
        _validate_publication_journals(
            match_root, summary, manifest, decisions, dice_manifest, consumption,
            requests, results, expected_engine_by_seat, identity, match_side,
            roll_count, files_per_match, configured_targets,
        )
    return summary


def _native_action_projection(action: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "action": action.get("action"),
        "physical_seat": action.get("physical_seat"),
    }
    if action.get("action") == "checker":
        projected.update({"dice": action.get("dice"), "moves": action.get("moves")})
    return projected


def _expected_terminal_event(
    game: Mapping[str, Any], expected_engine_by_seat: Mapping[str, str],
) -> dict[str, Any]:
    terminal = game["terminal"]
    kind = terminal["kind"]
    winner = game["winner_physical_seat"]
    event: dict[str, Any] = {
        "kind": kind,
        "winner_physical_seat": winner,
        "winner_engine": expected_engine_by_seat[winner],
        "points": game["points"],
        "result_level": terminal["result_level"],
    }
    if kind == "drop":
        loser = "X" if winner == "O" else "O"
        event.update({
            "loser_physical_seat": loser,
            "loser_engine": expected_engine_by_seat[loser],
        })
    elif kind == "resignation":
        event["resignation_level"] = terminal["result_level"]
    return event


def _validate_automatic_publication_transition(
    evidence: Any, from_gnuid: str, following_gnuid: str,
) -> None:
    if not isinstance(evidence, dict) or set(evidence) != {"type", "from_gnuid", "to_gnuid"}:
        raise MatchExecutionError("automatic publication transition evidence is malformed")
    if (
        evidence["type"] != "resignation_offer"
        or evidence["from_gnuid"] != from_gnuid
        or evidence["to_gnuid"] != following_gnuid
    ):
        raise MatchExecutionError("automatic publication transition identity is invalid")
    before = _decode_publication_gnuid(from_gnuid)
    after = _decode_publication_gnuid(following_gnuid)
    before_pending = before.cube.pending_action
    after_pending = after.cube.pending_action
    if (
        _board_snapshot(before) != _board_snapshot(after)
        or _score_snapshot(before) != _score_snapshot(after)
        or before.cube.value != after.cube.value
        or before.cube.owner != after.cube.owner
        or before.state.game_state != "playing"
        or after.state.game_state != "playing"
        or before.state.dice is not None
        or after.state.dice is not None
        or before.state.on_roll != after.state.on_roll
        or before.state.decision_player != before.state.on_roll
        or before_pending.type != "none"
        or after_pending.type != "resignation"
        or after_pending.offerer != before.state.on_roll
        or after_pending.responder == before.state.on_roll
        or after.state.decision_player != after_pending.responder
        or after_pending.resignation_multiplier not in {1, 2, 3}
    ):
        raise MatchExecutionError("automatic resignation transition changes unrelated GNU state")


def _validate_publication_decisions(
    summary: Mapping[str, Any],
    decisions: list[dict[str, Any]],
    consumption: list[dict[str, Any]],
    expected_engine_by_seat: Mapping[str, str],
    identity: PairIdentity,
    match_side: str,
) -> None:
    if match_side not in {"A", "B"}:
        raise MatchExecutionError("decision publication authority has an invalid match side")
    expected_authority = {
        "campaign_id": identity.campaign_id,
        "pair_id": identity.pair_id,
        "pair_index": identity.pair_index,
        "pair_member": match_side,
        "match_side": match_side,
    }
    observed_games: list[int] = []
    journal_actions: dict[int, list[dict[str, Any]]] = {
        game["game_number"]: [] for game in summary["games"]
    }
    terminals: dict[int, list[int]] = {number: [] for number in journal_actions}
    opening_records = {
        number: [
            record for record in consumption
            if record.get("game_number") == number and record.get("prompt_type") == "opening"
        ]
        for number in journal_actions
    }
    checker_records = {
        number: [
            record for record in consumption
            if record.get("game_number") == number and record.get("prompt_type") == "checker"
        ]
        for number in journal_actions
    }
    checker_offsets = {number: 0 for number in journal_actions}
    action_ordinals = {number: 0 for number in journal_actions}
    connected_gnuid: str | None = None
    for index, record in enumerate(decisions, 1):
        game_number = record.get("game_number")
        seat = record.get("physical_seat")
        transition = record.get("transition_evidence")
        if (
            any(record.get(key) != value for key, value in expected_authority.items())
            or record.get("record_ordinal") != index
            or record.get("decision_ordinal") != index
            or type(game_number) is not int
            or game_number not in journal_actions
            or seat not in {"O", "X"}
            or record.get("engine") != expected_engine_by_seat[seat]
            or not isinstance(record.get("gnuid"), str)
            or not record["gnuid"]
            or not isinstance(transition, dict)
        ):
            raise MatchExecutionError("decision journal authority or ordered record identity is invalid")
        observed_games.append(game_number)
        game = summary["games"][game_number - 1]
        command = record.get("command")
        command_type = transition.get("command_type")
        expected_command_type = (
            "pass" if command == "pass" else
            "accepted_resignation" if command == "accept" else
            "checker" if isinstance(command, str) and command not in {"roll", "double", "take"}
            else command
        )
        represented_in_native = expected_command_type in {"checker", "double", "take", "pass"}
        if represented_in_native:
            action_ordinals[game_number] += 1
            expected_action_ordinal: int | None = action_ordinals[game_number]
        else:
            expected_action_ordinal = None
        pre = transition.get("pre_command")
        post = transition.get("post_command")
        if (
            command_type != expected_command_type
            or record.get("action_ordinal") != expected_action_ordinal
            or transition.get("acting_physical_seat") != seat
            or transition.get("acting_engine") != expected_engine_by_seat[seat]
            or transition.get("game_number") != game_number
            or not isinstance(pre, dict)
            or pre.get("gnuid") != record["gnuid"]
            or pre.get("score") != game["start_score"]
            or not isinstance(post, dict)
            or not isinstance(post.get("gnuid"), str)
            or not post["gnuid"]
            or post["gnuid"] == pre["gnuid"]
        ):
            raise MatchExecutionError("decision transition command, actor, or pre/post state is invalid")
        if connected_gnuid is not None and pre["gnuid"] != connected_gnuid:
            raise MatchExecutionError("decision journal GNUIDs do not form one connected state history")
        try:
            pre_position = _decode_publication_gnuid(pre["gnuid"])
            post_position = _decode_publication_gnuid(post["gnuid"])
        except MatchExecutionError as exc:
            raise MatchExecutionError("decision journal contains an invalid publication GNUID") from exc
        event = transition.get("terminal_event")
        subsequent = transition.get("subsequent_opening_state")
        if event is None:
            if post.get("score") != game["start_score"] or subsequent is not None:
                raise MatchExecutionError("non-terminal decision changes score or carries an opening")
        else:
            terminals[game_number].append(index - 1)
            if event != _expected_terminal_event(game, expected_engine_by_seat):
                raise MatchExecutionError("decision terminal kind/level/result conflicts with native evidence")
            if post.get("score") != game["post_score"]:
                raise MatchExecutionError("decision terminal post-command score conflicts with native evidence")
            kind = event["kind"]
            if (
                kind == "ordinary_game_over" and (
                    command_type != "checker" or seat != game["winner_physical_seat"]
                )
                or kind == "drop" and (
                    command_type != "pass" or seat == game["winner_physical_seat"]
                )
                or kind == "resignation" and (
                    command_type != "accepted_resignation" or seat != game["winner_physical_seat"]
                )
            ):
                raise MatchExecutionError("decision terminal command or acting seat has wrong semantics")
            if game_number == summary["game_count"]:
                if subsequent is not None:
                    raise MatchExecutionError("match-final terminal unexpectedly carries a subsequent opening")
            else:
                next_game = summary["games"][game_number]
                expected_opening = {
                    "game_number": game_number + 1,
                    "gnuid": post["gnuid"],
                    "score": game["post_score"],
                    "on_roll_physical_seat": next_game["opening_state"]["on_roll_physical_seat"],
                    "decision_physical_seat": next_game["opening_state"]["on_roll_physical_seat"],
                    "dice": next_game["opening_state"]["dice"],
                }
                if subsequent != expected_opening:
                    raise MatchExecutionError("decision terminal lacks the exact next-game opening state")
                if index >= len(decisions):
                    raise MatchExecutionError("decision terminal has no immediately following game record")
                following = decisions[index]
                if (
                    following.get("game_number") != game_number + 1
                    or following.get("gnuid") != subsequent["gnuid"]
                    or not isinstance(following.get("transition_evidence"), dict)
                    or following["transition_evidence"].get("pre_command", {}).get("score") != game["post_score"]
                ):
                    raise MatchExecutionError("subsequent opening is reordered or belongs to the wrong game")

        consumed: list[dict[str, Any]] = []
        if event is not None and game_number < summary["game_count"]:
            consumed = opening_records[game_number + 1]
        elif command_type == "roll" or (
            command_type in {"checker", "take"} and post_position.state.dice is not None
        ):
            offset = checker_offsets[game_number]
            available = checker_records[game_number]
            if offset >= len(available):
                raise MatchExecutionError("decision command lacks its deterministic checker roll")
            consumed = [available[offset]]
            checker_offsets[game_number] += 1
        if event is not None and game_number < summary["game_count"]:
            final_opening = consumed[-2:]
            opener = "O" if final_opening[0]["die1"] > final_opening[1]["die1"] else "X"
            expected_next_roll_seat = _opposite_seat(opener)
        elif command_type == "roll":
            expected_next_roll_seat = _opposite_seat(seat)
        elif command_type == "double":
            expected_next_roll_seat = seat
        elif consumed and consumed[0].get("prompt_type") == "checker":
            expected_next_roll_seat = _opposite_seat(consumed[0]["physical_seat"])
        else:
            expected_next_roll_seat = (
                _seat(post_position.state.on_roll)
                if post_position.state.on_roll is not None else None
            )
        _validate_command_transition(
            str(command), pre_position, post_position, consumed, game_number, seat,
            expected_engine_by_seat[seat], expected_engine_by_seat,
            expected_next_roll_seat, event,
        )

        automatic = transition.get("automatic_transition")
        if automatic is None:
            connected_gnuid = post["gnuid"]
        else:
            if index >= len(decisions):
                raise MatchExecutionError("automatic transition has no following decision")
            following_gnuid = decisions[index].get("gnuid")
            _validate_automatic_publication_transition(
                automatic, post["gnuid"], following_gnuid
            )
            connected_gnuid = following_gnuid

        if command_type == "checker":
            try:
                moves = _parse_text_moves(str(command))
            except MatchExecutionError as exc:
                raise MatchExecutionError("decision checker command is not canonicalizable") from exc
            dice = record.get("analysis_dice")
            if (
                record.get("decision_type") != "checker"
                or not isinstance(dice, list) or len(dice) != 2
                or any(type(value) is not int or not 1 <= value <= 6 for value in dice)
            ):
                raise MatchExecutionError("decision checker dice/type evidence is invalid")
            journal_actions[game_number].append({
                "action": "checker", "physical_seat": seat, "dice": dice, "moves": moves,
            })
        elif command_type in {"double", "take", "pass"}:
            if record.get("decision_type") != "cube" or record.get("analysis_dice") is not None:
                raise MatchExecutionError("decision cube type/dice evidence is invalid")
            journal_actions[game_number].append({
                "action": "drop" if command_type == "pass" else command_type,
                "physical_seat": seat,
            })
        elif command_type == "roll":
            if record.get("decision_type") != "cube" or record.get("analysis_dice") is not None:
                raise MatchExecutionError("decision roll type/dice evidence is invalid")
        elif command_type == "accepted_resignation":
            if record.get("decision_type") != "board-rule" or record.get("analysis_dice") is not None:
                raise MatchExecutionError("decision resignation type/dice evidence is invalid")
        else:
            raise MatchExecutionError("decision journal has an unsupported command type")

    if observed_games != sorted(observed_games):
        raise MatchExecutionError("decision journal game records are reordered")
    if any(checker_offsets[number] != len(checker_records[number]) for number in checker_records):
        raise MatchExecutionError("deterministic checker rolls do not map exactly to decisions")
    for game in summary["games"]:
        number = game["game_number"]
        if len(terminals[number]) != 1:
            raise MatchExecutionError("decision journal lacks one exact ordered terminal per game")
        terminal_index = terminals[number][0]
        if any(record.get("game_number") == number for record in decisions[terminal_index + 1:]):
            raise MatchExecutionError("decision journal contains records after a game's terminal")
        expected_actions = [_native_action_projection(action) for action in game["actions"]]
        if journal_actions[number] != expected_actions:
            raise MatchExecutionError("decision journal actions do not match complete native game sequence")


def _validate_publication_dice(
    match_root: Path,
    summary: Mapping[str, Any],
    dice_manifest: Mapping[str, Any],
    consumption: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    expected_engine_by_seat: Mapping[str, str],
    identity: PairIdentity,
    match_side: str,
    roll_count: int,
    files_per_match: int,
) -> None:
    seed = namespace_seed(identity.base_seed, match_side)
    if (
        dice_manifest.get("schema_version") != DICE_SCHEMA_VERSION
        or dice_manifest.get("namespace") != match_side
        or dice_manifest.get("namespace_seed") != seed
        or dice_manifest.get("base_seed") != identity.base_seed
        or dice_manifest.get("pair_id") != identity.pair_id
        or dice_manifest.get("roll_count") != roll_count
        or dice_manifest.get("files_per_match") != files_per_match
    ):
        raise MatchExecutionError("deterministic dice manifest conflicts with frozen pair authority")
    streams = dice_manifest["streams"]
    stream_keys: set[tuple[int, str]] = set()
    active_games = {game["game_number"] for game in summary["games"]}
    for stream in streams:
        if not isinstance(stream, dict):
            raise MatchExecutionError("deterministic dice stream identity is malformed")
        game_number = stream.get("game_number")
        seat = stream.get("physical_seat")
        if type(game_number) is not int or not 1 <= game_number <= files_per_match or seat not in {"O", "X"}:
            raise MatchExecutionError("deterministic dice stream game/seat identity is invalid")
        key = (game_number, seat)
        if key in stream_keys:
            raise MatchExecutionError("deterministic dice manifest duplicates a stream")
        stream_keys.add(key)
        expected_path = f"game_{game_number:03d}_seat_{seat}.csv"
        if (
            stream.get("namespace") != match_side
            or stream.get("namespace_seed") != seed
            or stream.get("base_seed") != identity.base_seed
            or stream.get("pair_id") != identity.pair_id
            or stream.get("pair_member") != match_side
            or stream.get("match_side") != match_side
            or stream.get("engine") != expected_engine_by_seat[seat]
            or stream.get("stream_id") != stream_id(seed, game_number, seat)
            or stream.get("path") != expected_path
            or not isinstance(stream.get("sha256"), str)
            or len(stream["sha256"]) != 64
        ):
            raise MatchExecutionError("deterministic dice stream conflicts with pair/seat authority")
        if game_number in active_games and stream["sha256"] != stream_sha256(
            seed, game_number, seat, roll_count
        ):
            raise MatchExecutionError("active deterministic dice stream hash conflicts with frozen authority")
        stream_path = match_root / "dice" / expected_path
        if stream_path.exists() and sha256_file(stream_path) != stream["sha256"]:
            raise MatchExecutionError("deterministic dice stream file conflicts with its manifest hash")
    if any((game_number, seat) not in stream_keys for game_number in active_games for seat in ("O", "X")):
        raise MatchExecutionError("deterministic dice manifest lacks an active native-game stream")

    game_records: dict[int, list[dict[str, Any]]] = {number: [] for number in active_games}
    last_game = 0
    for ordinal, record in enumerate(consumption, 1):
        game_number = record.get("game_number")
        seat = record.get("physical_seat")
        prompt_type = record.get("prompt_type")
        roll_index = record.get("roll_index")
        if (
            record.get("schema_version") != DICE_SCHEMA_VERSION
            or record.get("namespace") != match_side
            or record.get("namespace_seed") != seed
            or record.get("base_seed") != identity.base_seed
            or record.get("pair_id") != identity.pair_id
            or record.get("pair_member") != match_side
            or record.get("match_side") != match_side
            or record.get("consumption_ordinal") != ordinal
            or type(game_number) is not int
            or game_number not in active_games
            or game_number < last_game
            or seat not in {"O", "X"}
            or record.get("engine") != expected_engine_by_seat[seat]
            or prompt_type not in {"opening", "checker"}
            or type(roll_index) is not int
            or not 1 <= roll_index <= roll_count
            or record.get("stream_id") != stream_id(seed, game_number, seat)
            or record.get("stream_path") != f"game_{game_number:03d}_seat_{seat}.csv"
        ):
            raise MatchExecutionError("deterministic dice consumption ordering/authority is invalid")
        last_game = game_number
        authoritative = dice_record(seed, 1, 7, game_number, seat, roll_index)
        expected_dice = (
            (authoritative["opening_die"], None)
            if prompt_type == "opening"
            else (authoritative["die1"], authoritative["die2"])
        )
        if (record.get("die1"), record.get("die2")) != expected_dice:
            raise MatchExecutionError("deterministic dice values conflict with frozen stream authority")
        game_records[game_number].append(record)

    for game in summary["games"]:
        records = game_records[game["game_number"]]
        offset = 0
        opening_index = 1
        final_opening: tuple[dict[str, Any], dict[str, Any]] | None = None
        while offset + 1 < len(records):
            o_record, x_record = records[offset:offset + 2]
            if (
                o_record["prompt_type"] != "opening" or x_record["prompt_type"] != "opening"
                or o_record["physical_seat"] != "O" or x_record["physical_seat"] != "X"
                or o_record["roll_index"] != opening_index or x_record["roll_index"] != opening_index
            ):
                break
            final_opening = (o_record, x_record)
            offset += 2
            if o_record["die1"] != x_record["die1"]:
                break
            opening_index += 1
        if final_opening is None or final_opening[0]["die1"] == final_opening[1]["die1"]:
            raise MatchExecutionError("deterministic dice journal lacks a complete non-tied opening")
        if any(record["prompt_type"] == "opening" for record in records[offset:]):
            raise MatchExecutionError("deterministic opening records are reordered after checker rolls")
        opener = "O" if final_opening[0]["die1"] > final_opening[1]["die1"] else "X"
        opening_dice = [
            final_opening[0 if opener == "O" else 1]["die1"],
            final_opening[1 if opener == "O" else 0]["die1"],
        ]
        if game["opening_state"] != {
            "cube_value": 1, "on_roll_physical_seat": opener, "dice": opening_dice,
        }:
            raise MatchExecutionError("native opening does not match deterministic opening records")
        checker_indexes = {"O": 0, "X": 0}
        checker_consumption: list[dict[str, Any]] = []
        for record in records[offset:]:
            seat = record["physical_seat"]
            checker_indexes[seat] += 1
            if record["roll_index"] != checker_indexes[seat]:
                raise MatchExecutionError("deterministic checker roll indexes are reordered or duplicated")
            checker_consumption.append(record)
        native_checkers = [
            (action_ordinal, action)
            for action_ordinal, action in enumerate(game["actions"], 1)
            if action["action"] == "checker"
        ]
        decision_checkers = [
            record for record in decisions
            if record.get("game_number") == game["game_number"]
            and record.get("transition_evidence", {}).get("command_type") == "checker"
        ]
        if len(native_checkers) != len(decision_checkers) or len(native_checkers) != len(checker_consumption) + 1:
            raise MatchExecutionError("checker action evidence is incomplete across publication authorities")
        opening_by_seat = {record["physical_seat"]: record for record in final_opening}
        for checker_index, ((action_ordinal, native), decision) in enumerate(
            zip(native_checkers, decision_checkers)
        ):
            seat = native["physical_seat"]
            engine = expected_engine_by_seat[seat]
            if checker_index == 0:
                actor_opening = opening_by_seat[seat]
                opponent_opening = opening_by_seat[_opposite_seat(seat)]
                dice_identity = {
                    "game_number": game["game_number"], "action_ordinal": action_ordinal,
                    "physical_seat": seat, "engine": engine, "dice": opening_dice,
                    "stream_id": actor_opening["stream_id"],
                    "stream_path": actor_opening["stream_path"],
                    "roll_index": actor_opening["roll_index"],
                    "opposing_stream_id": opponent_opening["stream_id"],
                    "opposing_stream_path": opponent_opening["stream_path"],
                }
            else:
                consumed = checker_consumption[checker_index - 1]
                dice_identity = {
                    "game_number": game["game_number"], "action_ordinal": action_ordinal,
                    "physical_seat": consumed["physical_seat"], "engine": consumed["engine"],
                    "dice": [consumed["die1"], consumed["die2"]],
                    "stream_id": consumed["stream_id"], "stream_path": consumed["stream_path"],
                    "roll_index": consumed["roll_index"],
                }
            expected_stream = stream_id(seed, game["game_number"], seat)
            if (
                dice_identity["physical_seat"] != seat
                or dice_identity["engine"] != engine
                or dice_identity["dice"] != native["dice"]
                or dice_identity["stream_id"] != expected_stream
                or dice_identity["stream_path"] != f"game_{game['game_number']:03d}_seat_{seat}.csv"
                or decision.get("game_number") != dice_identity["game_number"]
                or decision.get("action_ordinal") != dice_identity["action_ordinal"]
                or decision.get("physical_seat") != dice_identity["physical_seat"]
                or decision.get("engine") != dice_identity["engine"]
                or decision.get("analysis_dice") != dice_identity["dice"]
            ):
                raise MatchExecutionError(
                    "checker dice semantic identity conflicts across decision, stream, and native evidence"
                )


def _validate_publication_analysis(
    requests: list[dict[str, Any]],
    results: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    expected_engine_by_seat: Mapping[str, str],
    identity: PairIdentity,
    match_side: str,
    configured_targets: Mapping[str, Mapping[str, str]],
) -> None:
    analyzed_decisions = [
        record for record in decisions if record.get("decision_type") in {"checker", "cube"}
    ]
    if len(requests) != len(results) or len(requests) != len(analyzed_decisions):
        raise MatchExecutionError("analysis journals do not map one-to-one to decisions")
    expected_authority = {
        "campaign_id": identity.campaign_id,
        "pair_id": identity.pair_id,
        "pair_index": identity.pair_index,
        "pair_member": match_side,
        "match_side": match_side,
    }
    previous_decision_ordinal = 0
    for request_ordinal, (request, result, decision) in enumerate(
        zip(requests, results, analyzed_decisions), 1
    ):
        decision_ordinal = decision.get("decision_ordinal")
        seat = decision.get("physical_seat")
        engine = decision.get("engine")
        decision_type = decision.get("decision_type")
        expected_context = {
            **expected_authority,
            "game_number": decision.get("game_number"),
            "request_ordinal": request_ordinal,
            "decision_ordinal": decision_ordinal,
            "physical_seat": seat,
            "engine": engine,
            "decision_type": decision_type,
            "gnuid": decision.get("gnuid"),
            "dice": decision.get("analysis_dice"),
        }
        if (
            type(decision_ordinal) is not int
            or decision_ordinal <= previous_decision_ordinal
            or seat not in {"O", "X"}
            or engine != expected_engine_by_seat[seat]
            or request != expected_context
            or any(result.get(key) != value for key, value in expected_context.items())
            or set(result) != {*expected_context, "returned_result"}
        ):
            raise MatchExecutionError("analysis journal ordering or decision association is invalid")
        previous_decision_ordinal = decision_ordinal
        raw = result.get("returned_result")
        validated = decision.get("engine_kit_result")
        if not isinstance(raw, dict) or not isinstance(validated, dict):
            raise MatchExecutionError("analysis result evidence is malformed")
        validated_without_depth = dict(validated)
        depth = validated_without_depth.pop("campaign_depth_evidence", None)
        if validated_without_depth != raw or not isinstance(depth, dict):
            raise MatchExecutionError("raw and validated Engine Kit result evidence conflict")
        expected_target = configured_targets.get(str(engine), {}).get(str(decision_type))
        if (
            not isinstance(expected_target, str)
            or re.fullmatch(r"\d+ply", expected_target) is None
            or raw.get("status") != "complete"
            or raw.get("position") != {"id": decision["gnuid"], "format": "gnuid"}
            or raw.get("decision_type") != decision_type
            or not isinstance(raw.get("engine"), dict)
            or raw["engine"].get("name") != engine
            or raw["engine"].get("analysis_setting") != expected_target
            or not isinstance(raw.get("raw_source"), dict)
            or not raw["raw_source"]
        ):
            raise MatchExecutionError("analysis result conflicts with request/configured authority")
        decision_key = f"{decision_type}_decision"
        decision_evidence = raw.get(decision_key)
        other_key = "cube_decision" if decision_type == "checker" else "checker_decision"
        if not isinstance(decision_evidence, dict) or raw.get(other_key) is not None:
            raise MatchExecutionError("analysis result carries malformed decision semantics")
        actual_ply = decision_evidence.get("actual_ply")
        candidate_actuals: list[int | None] | None = None
        if decision_type == "checker":
            candidates = decision_evidence.get("candidates")
            recommended = decision_evidence.get("recommended_move_id")
            if (
                not isinstance(candidates, list)
                or not candidates
                or not isinstance(recommended, str)
                or any(not isinstance(candidate, dict) for candidate in candidates)
                or len({candidate.get("move_id") for candidate in candidates}) != len(candidates)
                or recommended not in {candidate.get("move_id") for candidate in candidates}
            ):
                raise MatchExecutionError("checker analysis result/candidates are malformed")
            candidate_actuals = [candidate.get("actual_ply") for candidate in candidates]
        elif not isinstance(decision_evidence.get("recommendation"), str):
            raise MatchExecutionError("cube analysis result is malformed")
        configured_ply = int(expected_target.removesuffix("ply"))
        expected_depth = {
            "configured_target": expected_target,
            "recommended_actual_ply": actual_ply,
            "candidate_actual_plies": candidate_actuals,
        }
        if depth != expected_depth:
            raise MatchExecutionError("decision depth evidence conflicts with raw analysis result")
        try:
            validate_actual_depth_evidence(
                str(engine), str(decision_type), configured_ply, actual_ply, candidate_actuals
            )
        except EngineKitMismatch as exc:
            raise MatchExecutionError("analysis result violates the live depth policy") from exc


def _validate_publication_journals(
    match_root: Path,
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    decisions: list[dict[str, Any]],
    dice_manifest: Mapping[str, Any],
    consumption: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    results: list[dict[str, Any]],
    expected_engine_by_seat: Mapping[str, str],
    identity: PairIdentity,
    match_side: str,
    roll_count: int,
    files_per_match: int,
    configured_targets: Mapping[str, Mapping[str, str]],
) -> None:
    if (
        manifest.get("side") != match_side
        or manifest.get("pair_member") != match_side
        or manifest.get("namespace_seed") != namespace_seed(identity.base_seed, match_side)
        or manifest.get("analysis_request_evidence") != "analysis_requests.jsonl"
        or manifest.get("analysis_result_evidence") != "analysis_results.jsonl"
    ):
        raise MatchExecutionError("match manifest conflicts with publication pair/side authority")
    required_outputs = {
        "native/match.sgf", "native/match.txt", "decisions.jsonl",
        "dice/seat_dice_manifest.json", "dice/seat_dice_consumption.jsonl",
        "analysis_requests.jsonl", "analysis_results.jsonl",
    }
    output_hashes = manifest.get("output_sha256")
    if not isinstance(output_hashes, dict) or set(output_hashes) != required_outputs:
        raise MatchExecutionError("match manifest output hash inventory is incomplete")
    for relative in sorted(required_outputs):
        path = match_root / relative
        if not path.is_file() or output_hashes.get(relative) != sha256_file(path):
            raise MatchExecutionError("match manifest output hash conflicts with publication file")
    _validate_publication_dice(
        match_root, summary, dice_manifest, consumption, decisions, expected_engine_by_seat,
        identity, match_side, roll_count, files_per_match,
    )
    _validate_publication_decisions(
        summary, decisions, consumption, expected_engine_by_seat, identity, match_side
    )
    _validate_publication_analysis(
        requests, results, decisions, expected_engine_by_seat, identity, match_side,
        configured_targets,
    )


def _validate_opening_transition(
    consumed: list[dict[str, Any]],
    game_number: int,
    position: Any,
    engine_by_seat: Mapping[str, str],
    expected_next_roll_seat: str | None,
    expected_score: tuple[int, int],
) -> str:
    if not consumed or len(consumed) % 2:
        raise MatchExecutionError("GNU opening prompt consumption is missing or incomplete")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for offset in range(0, len(consumed), 2):
        o_entry, x_entry = consumed[offset : offset + 2]
        entries = (("O", o_entry), ("X", x_entry))
        for seat, entry in entries:
            die = entry.get("die1")
            if (
                entry.get("prompt_type") != "opening"
                or entry.get("game_number") != game_number
                or type(entry.get("game_number")) is not int
                or entry.get("physical_seat") != seat
                or entry.get("engine") != engine_by_seat.get(seat)
                or entry.get("die2") is not None
                or type(die) is not int
                or not 1 <= die <= 6
            ):
                raise MatchExecutionError("GNU opening prompt was misparsed or bound to the wrong game/seat")
        attempt = offset // 2 + 1
        if any(type(entry.get("roll_index")) is not int or entry.get("roll_index") != attempt for entry in (o_entry, x_entry)):
            raise MatchExecutionError("GNU opening attempts are not a complete deterministic sequence")
        pairs.append((o_entry, x_entry))
    if any(o_entry["die1"] != x_entry["die1"] for o_entry, x_entry in pairs[:-1]):
        raise MatchExecutionError("GNU opening consumption has a non-final non-tied pair")
    final_o, final_x = pairs[-1]
    if final_o["die1"] == final_x["die1"]:
        raise MatchExecutionError("GNU opening consumption lacks one final non-tied pair")
    winner = "O" if final_o["die1"] > final_x["die1"] else "X"
    loser = "X" if winner == "O" else "O"
    winner_player = _player(winner)
    try:
        on_roll = _seat(position.state.on_roll)
        decision_player = _seat(position.state.decision_player)
        raw_observed_dice = position.state.dice
    except (AttributeError, TypeError, ValueError) as exc:
        raise MatchExecutionError("GNU opening board state is missing or malformed") from exc
    if (
        not isinstance(raw_observed_dice, (list, tuple))
        or len(raw_observed_dice) != 2
        or any(type(value) is not int or not 1 <= value <= 6 for value in raw_observed_dice)
    ):
        raise MatchExecutionError("GNU opening board state is missing or malformed")
    observed_dice = tuple(raw_observed_dice)
    expected_dice = (
        int(final_o["die1"] if winner == "O" else final_x["die1"]),
        int(final_x["die1"] if winner == "O" else final_o["die1"]),
    )
    if on_roll != winner or decision_player != winner:
        raise MatchExecutionError("GNU opening winner differs from the decision/on-roll physical seat")
    if observed_dice != expected_dice:
        raise MatchExecutionError("GNU opening board dice differ from the consumed opening dice")
    if expected_next_roll_seat != loser:
        raise MatchExecutionError("GNU opening dice were misapplied to the physical-seat stream")
    if (
        set(engine_by_seat) != {"O", "X"}
        or set(engine_by_seat.values()) != {"sage", "gnu"}
        or engine_by_seat.get(winner) not in {"sage", "gnu"}
        or position.state.game_state != "playing"
        or position.state.on_roll != winner_player
        or position.state.decision_player != winner_player
        or not _is_starting_board(position)
        or _score_snapshot(position) != (*expected_score, 7)
        or _cube_snapshot(position) != (1, "center", "none", None, None, None, None)
    ):
        raise MatchExecutionError("GNU opening state is not the complete legal new-game state")
    return winner


def _board_environment(environment: dict[str, str], isolated_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(environment)
    env["HOME"] = str(Path(isolated_home).resolve())
    return env


class MatchExecutionError(RuntimeError):
    """The neutral board process or verified Engine Kit decision failed."""


class GnuBoardProcess:
    """Use pinned GNU only as a two-human board/rules process, never as evaluator."""

    def __init__(
        self,
        executable: Path,
        environment: dict[str, str],
        dice: SeatDiceController,
        isolated_home: Path,
    ):
        self.isolated_home = Path(isolated_home).resolve()
        self._owns_isolated_home = False
        self.master_fd = -1
        try:
            self.isolated_home.mkdir(parents=False, exist_ok=False)
            self._owns_isolated_home = True
            env = _board_environment(environment, self.isolated_home)
            self.master_fd, slave_fd = pty.openpty()
            try:
                attributes = termios.tcgetattr(slave_fd)
                attributes[3] &= ~termios.ECHO
                termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)
                self.process = subprocess.Popen(
                    [str(executable), "-q", "-t"],
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=env,
                    close_fds=True,
                )
            except BaseException as startup_error:
                try:
                    os.close(slave_fd)
                except BaseException as close_error:
                    startup_error.add_note(
                        "GNU board slave-PTY cleanup also failed: "
                        f"{type(close_error).__name__}: {close_error}"
                    )
                raise
            else:
                os.close(slave_fd)
            self.dice = dice
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.master_fd, selectors.EVENT_READ)
            self.transcript: list[dict[str, str]] = []
            self._read_until_prompt("<startup>")
        except BaseException as startup_error:
            try:
                self._cleanup(terminate=True)
            except BaseException as cleanup_error:
                startup_error.add_note(
                    "GNU board constructor cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def _cleanup(self, *, terminate: bool) -> None:
        cleanup_errors: list[BaseException] = []
        process = getattr(self, "process", None)
        try:
            running = process is not None and process.poll() is None
        except BaseException as exc:
            cleanup_errors.append(exc)
            running = False
        if process is not None and terminate and running:
            try:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
            except BaseException as exc:
                cleanup_errors.append(exc)
                try:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5.0)
                except BaseException as fallback_exc:
                    cleanup_errors.append(fallback_exc)
        selector = getattr(self, "selector", None)
        if selector is not None:
            try:
                selector.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if getattr(self, "master_fd", -1) >= 0:
            try:
                os.close(self.master_fd)
            except BaseException as exc:
                cleanup_errors.append(exc)
            finally:
                self.master_fd = -1
        if getattr(self, "_owns_isolated_home", False):
            try:
                if self.isolated_home.exists():
                    shutil.rmtree(self.isolated_home)
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                home_survived = self.isolated_home.exists()
            except BaseException as exc:
                cleanup_errors.append(exc)
                home_survived = True
            if home_survived:
                cleanup_errors.append(MatchExecutionError("GNU isolated HOME survived cleanup"))
            else:
                self._owns_isolated_home = False
        if cleanup_errors:
            for additional_error in cleanup_errors[1:]:
                cleanup_errors[0].add_note(
                    "additional GNU board cleanup failure: "
                    f"{type(additional_error).__name__}: {additional_error}"
                )
            raise cleanup_errors[0]

    def _read_until_prompt(self, command: str, timeout_seconds: float = 60.0) -> str:
        deadline = time.monotonic() + timeout_seconds
        buffer = b""
        search_from = 0
        context_start = 0
        while time.monotonic() < deadline:
            events = self.selector.select(timeout=min(1.0, max(0.0, deadline - time.monotonic())))
            if not events:
                if self.process.poll() is not None:
                    raise MatchExecutionError(f"GNU board process exited during {command!r}")
                continue
            try:
                chunk = os.read(self.master_fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    detail = ANSI_RE.sub("", buffer.decode("utf-8", "replace")).replace("\r", "").strip()
                    raise MatchExecutionError(
                        f"GNU board process closed PTY during {command!r}; "
                        f"returncode={self.process.poll()}: {detail[-1000:]}"
                    ) from exc
                raise
            if not chunk:
                raise MatchExecutionError(f"GNU board process closed output during {command!r}")
            buffer += chunk
            dice_index = buffer.find(MANUAL_DICE, search_from)
            if dice_index >= 0:
                context = ANSI_RE.sub(
                    "",
                    buffer[context_start : dice_index + len(MANUAL_DICE)].decode("utf-8", "replace"),
                )
                die1, die2 = self.dice.dice_for_prompt(context)
                os.write(self.master_fd, f"{die1} {die2}\n".encode("ascii"))
                search_from = dice_index + len(MANUAL_DICE)
                context_start = search_from
                continue
            if PROMPT_RE.search(buffer):
                text = ANSI_RE.sub("", buffer.decode("utf-8", "replace")).replace("\r", "")
                self.transcript.append({"command": command, "output": text})
                return text
        raise MatchExecutionError(f"GNU board process timed out during {command!r}")

    def send(self, command: str, timeout_seconds: float = 60.0) -> str:
        os.write(self.master_fd, (command + "\n").encode("utf-8"))
        output = self._read_until_prompt(command, timeout_seconds)
        _raise_on_gnu_error(command, output)
        return output

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                try:
                    self.send("quit", timeout_seconds=10.0)
                except Exception:
                    pass
        finally:
            self._cleanup(terminate=True)


def _gnuid(board: str) -> str:
    positions = POSITION_RE.findall(board)
    matches = MATCH_RE.findall(board)
    if not positions or not matches:
        raise MatchExecutionError("GNU board output lacks Position ID or Match ID")
    return f"{positions[-1]}:{matches[-1]}"


def _seat(player: str) -> str:
    if player == "player_0":
        return "O"
    if player == "player_1":
        return "X"
    raise MatchExecutionError(f"unsupported player identity: {player}")


def _player(seat: str) -> str:
    if seat == "O":
        return "player_0"
    if seat == "X":
        return "player_1"
    raise MatchExecutionError(f"unsupported physical seat: {seat}")


def _opposite_seat(seat: str) -> str:
    if seat == "O":
        return "X"
    if seat == "X":
        return "O"
    raise MatchExecutionError(f"unsupported physical seat: {seat}")


def _board_snapshot(position: Any) -> tuple[Any, ...]:
    try:
        board = position.board
        return (
            int(board.checker_count.player_0),
            int(board.checker_count.player_1),
            tuple(int(value) for value in board.player_0.points),
            int(board.player_0.bar),
            int(board.player_0.off),
            tuple(int(value) for value in board.player_1.points),
            int(board.player_1.bar),
            int(board.player_1.off),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise MatchExecutionError("GNU board checker state is missing or malformed") from exc


def _cube_snapshot(position: Any) -> tuple[Any, ...]:
    try:
        pending = position.cube.pending_action
        return (
            position.cube.value,
            position.cube.owner,
            pending.type,
            pending.offerer,
            pending.responder,
            pending.offered_cube_value,
            pending.resignation_multiplier,
        )
    except AttributeError as exc:
        raise MatchExecutionError("GNU cube state is missing or malformed") from exc


def _score_snapshot(position: Any) -> tuple[int, int, int]:
    try:
        return (
            int(position.score.player_0),
            int(position.score.player_1),
            int(position.score.match_length),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise MatchExecutionError("GNU match score is missing or malformed") from exc


def _validate_cube_value(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0 or value & (value - 1):
        raise MatchExecutionError(f"GNU {label} cube value is missing or malformed")
    return value


def _simulate_checker_move(position: Any, physical_seat: str, notation: str) -> tuple[Any, ...]:
    """Apply ordinary GNU move notation to a canonical self-relative board."""
    snapshot = _board_snapshot(position)
    checker_counts = [snapshot[0], snapshot[1]]
    points = [list(snapshot[2]), list(snapshot[5])]
    bars = [snapshot[3], snapshot[6]]
    offs = [snapshot[4], snapshot[7]]
    actor = 0 if physical_seat == "O" else 1
    opponent = 1 - actor
    raw_dice = position.state.dice
    if (
        not isinstance(raw_dice, (list, tuple))
        or len(raw_dice) != 2
        or any(type(value) is not int or not 1 <= value <= 6 for value in raw_dice)
    ):
        raise MatchExecutionError("GNU checker command has missing or malformed prior dice")
    available_dice = list(raw_dice) * (2 if raw_dice[0] == raw_dice[1] else 1)
    movement_dice: list[set[int]] = []
    tokens = notation.split()
    if not tokens:
        raise MatchExecutionError("GNU checker command is empty")
    for token in tokens:
        multiplier = 1
        repeated = re.search(r"\((\d+)\)$", token)
        if repeated is not None:
            multiplier = int(repeated.group(1))
            token = token[: repeated.start()]
        locations = [part.rstrip("*").lower() for part in token.split("/")]
        if (
            multiplier <= 0
            or len(locations) < 2
            or locations[0] == "off"
            or any(
                location not in {"bar", "off"}
                and (not location.isdigit() or not 1 <= int(location) <= 24)
                for location in locations
            )
        ):
            raise MatchExecutionError(f"unsupported GNU checker notation: {notation!r}")
        for _ in range(multiplier):
            for source, destination in zip(locations, locations[1:]):
                if source == "bar":
                    source_number = 25
                    if bars[actor] <= 0:
                        raise MatchExecutionError("GNU checker notation moves an absent bar checker")
                    bars[actor] -= 1
                elif source == "off":
                    raise MatchExecutionError("GNU checker notation moves a borne-off checker")
                else:
                    source_number = int(source)
                    source_index = int(source) - 1
                    if points[actor][source_index] <= 0:
                        raise MatchExecutionError("GNU checker notation moves an absent checker")
                    points[actor][source_index] -= 1
                if destination == "bar":
                    raise MatchExecutionError("GNU checker notation cannot move to the bar")
                if destination == "off":
                    allowed = {source_number}
                    if not any(points[actor][source_number:]):
                        allowed.update(die for die in available_dice if die > source_number)
                    movement_dice.append(allowed)
                    offs[actor] += 1
                    continue
                destination_index = int(destination) - 1
                distance = source_number - int(destination)
                if distance <= 0:
                    raise MatchExecutionError("GNU checker notation moves in the wrong direction")
                movement_dice.append({distance})
                opponent_index = 23 - destination_index
                if points[opponent][opponent_index] > 1:
                    raise MatchExecutionError("GNU checker notation lands on a blocked point")
                if points[opponent][opponent_index] == 1:
                    points[opponent][opponent_index] = 0
                    bars[opponent] += 1
                points[actor][destination_index] += 1
    if len(movement_dice) > len(available_dice):
        raise MatchExecutionError("GNU checker notation uses more moves than the prior dice permit")

    def dice_can_cover(index: int, remaining: list[int]) -> bool:
        if index == len(movement_dice):
            return True
        for die in sorted(movement_dice[index]):
            if die in remaining:
                reduced = list(remaining)
                reduced.remove(die)
                if dice_can_cover(index + 1, reduced):
                    return True
        return False

    if not dice_can_cover(0, available_dice):
        raise MatchExecutionError("GNU checker notation does not agree with the prior dice")
    return (
        checker_counts[0], checker_counts[1], tuple(points[0]), bars[0], offs[0],
        tuple(points[1]), bars[1], offs[1],
    )


def _is_starting_board(position: Any) -> bool:
    points = (0, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2)
    return _board_snapshot(position) == (15, 15, points, 0, 0, points, 0, 0)


def _validate_consumed_checker_roll(
    consumed: list[dict[str, Any]],
    game_number: int,
    expected_seat: str,
    engine_by_seat: Mapping[str, str],
    expected_next_roll_seat: str | None,
    *,
    required: bool,
) -> tuple[int, int] | None:
    if (required and len(consumed) != 1) or (not required and len(consumed) > 1):
        raise MatchExecutionError("GNU command consumed an impossible number of manual dice records")
    if not consumed:
        if required:
            raise MatchExecutionError("GNU roll did not consume exactly one manual dice record")
        if expected_next_roll_seat != expected_seat:
            raise MatchExecutionError("GNU manual-dice controller expected the wrong physical seat")
        return None
    entry = consumed[0]
    die1, die2 = entry.get("die1"), entry.get("die2")
    if (
        entry.get("prompt_type") != "checker"
        or entry.get("game_number") != game_number
        or type(entry.get("game_number")) is not int
        or entry.get("physical_seat") != expected_seat
        or entry.get("engine") != engine_by_seat.get(expected_seat)
        or type(die1) is not int
        or type(die2) is not int
        or not 1 <= die1 <= 6
        or not 1 <= die2 <= 6
    ):
        raise MatchExecutionError("GNU manual dice were consumed for the wrong game, physical seat, or engine")
    if expected_next_roll_seat != _opposite_seat(expected_seat):
        raise MatchExecutionError("GNU manual-dice consumption left the wrong physical seat next")
    return die1, die2


def _validate_command_transition(
    command: str,
    position: Any,
    next_position: Any,
    consumed: list[dict[str, Any]],
    game_number: int,
    physical_seat: str,
    engine: str,
    engine_by_seat: Mapping[str, str],
    expected_next_roll_seat: str | None,
    terminal_event: Mapping[str, Any] | None,
) -> None:
    """Fail closed unless a changed GNU ID represents the commanded transition."""
    if (
        set(engine_by_seat) != {"O", "X"}
        or set(engine_by_seat.values()) != {"sage", "gnu"}
        or engine_by_seat.get(physical_seat) != engine
    ):
        raise MatchExecutionError("GNU command actor is bound to the wrong physical-seat engine")
    actor = _player(physical_seat)
    other_seat = _opposite_seat(physical_seat)
    other = _player(other_seat)
    try:
        state = position.state
        next_state = next_position.state
        pending = position.cube.pending_action
    except AttributeError as exc:
        raise MatchExecutionError("GNU command state is missing or malformed") from exc
    if state.game_state != "playing" or state.decision_player != actor:
        raise MatchExecutionError("GNU command actor does not match the expected physical seat")
    previous_score = _score_snapshot(position)
    next_score = _score_snapshot(next_position)
    if previous_score[2] != 7 or next_score[2] != 7:
        raise MatchExecutionError("GNU command changed the frozen match length")
    previous_cube = _cube_snapshot(position)
    next_cube = _cube_snapshot(next_position)
    previous_board = _board_snapshot(position)
    next_board = _board_snapshot(next_position)
    cube_value = _validate_cube_value(position.cube.value, "current")
    empty_current_cube = (
        cube_value, position.cube.owner, "none", None, None, None, None
    )
    score_delta = (next_score[0] - previous_score[0], next_score[1] - previous_score[1])
    score_changed = score_delta != (0, 0)
    if score_changed != (terminal_event is not None):
        raise MatchExecutionError("GNU completed-game output/state transition is missing or misparsed")

    checker_command = command not in {"roll", "double", "take", "pass", "accept"}
    winner: str | None = None
    expected_points: int | None = None
    expected_result_level: int | None = None
    expected_final_board: tuple[Any, ...] | None = None
    if checker_command:
        if (
            previous_cube != empty_current_cube
            or state.on_roll != actor
            or not isinstance(state.dice, (list, tuple))
            or len(state.dice) != 2
            or any(type(value) is not int or not 1 <= value <= 6 for value in state.dice)
        ):
            raise MatchExecutionError("GNU checker command precondition is semantically invalid")
        expected_final_board = _simulate_checker_move(position, physical_seat, command)
        if expected_final_board == previous_board:
            raise MatchExecutionError("GNU checker command did not move a checker")
        winner = actor
        actor_offset = 4 if physical_seat == "O" else 7
        if expected_final_board[actor_offset] == expected_final_board[0 if physical_seat == "O" else 1]:
            loser_offset = 7 if physical_seat == "O" else 4
            loser_bar_offset = 6 if physical_seat == "O" else 3
            loser_points_offset = 5 if physical_seat == "O" else 2
            multiplier = 1
            if expected_final_board[loser_offset] == 0:
                multiplier = 3 if (
                    expected_final_board[loser_bar_offset] > 0
                    or any(expected_final_board[loser_points_offset][18:24])
                ) else 2
            expected_points = cube_value * multiplier
            expected_result_level = multiplier
        elif score_changed:
            raise MatchExecutionError("GNU checker command awarded points before bearing off all checkers")
    elif command in {"roll", "double"}:
        if (
            previous_cube != empty_current_cube
            or state.on_roll != actor
            or state.dice is not None
        ):
            raise MatchExecutionError(f"GNU {command} precondition is semantically invalid")
        if command == "double" and position.cube.owner not in {"center", actor}:
            raise MatchExecutionError("GNU double precondition has the wrong cube owner")
    elif command in {"take", "pass"}:
        expected_double = (
            cube_value, position.cube.owner, "double", other, actor,
            cube_value * 2, None,
        )
        if (
            previous_cube != expected_double
            or state.on_roll != other
            or state.dice is not None
            or position.cube.owner not in {"center", other}
        ):
            raise MatchExecutionError(f"GNU {command} precondition is semantically invalid")
        winner = other if command == "pass" else None
        expected_points = cube_value if command == "pass" else None
        expected_result_level = 1 if command == "pass" else None
    elif command == "accept":
        multiplier = pending.resignation_multiplier
        expected_resignation = (
            cube_value, position.cube.owner, "resignation", other, actor,
            None, multiplier,
        )
        if (
            previous_cube != expected_resignation
            or state.on_roll != other
            or state.dice is not None
            or type(multiplier) is not int
            or not 1 <= multiplier <= 3
        ):
            raise MatchExecutionError("GNU resignation precondition is semantically invalid")
        winner = actor
        expected_points = cube_value * multiplier
        expected_result_level = multiplier
    else:  # pragma: no cover - all strings are checker commands or listed above
        raise MatchExecutionError(f"unsupported GNU command transition: {command}")

    if score_changed:
        if winner is None or expected_points is None or consumed and not all(
            entry.get("prompt_type") == "opening" for entry in consumed
        ):
            raise MatchExecutionError("GNU command produced an invalid completed-game transition")
        winner_index = 0 if winner == "player_0" else 1
        expected_delta = [0, 0]
        expected_delta[winner_index] = expected_points
        if score_delta != tuple(expected_delta):
            raise MatchExecutionError("GNU command awarded the wrong score or winner")
        expected_winner_seat = _seat(winner)
        if (
            terminal_event is None
            or terminal_event.get("winner_physical_seat") != expected_winner_seat
            or terminal_event.get("winner_engine") != engine_by_seat[expected_winner_seat]
            or terminal_event.get("points") != expected_points
            or terminal_event.get("result_level") != expected_result_level
        ):
            raise MatchExecutionError("GNU terminal event has the wrong winner or points")
        if command == "pass":
            if (
                terminal_event.get("kind") != "drop"
                or terminal_event.get("loser_physical_seat") != physical_seat
                or terminal_event.get("loser_engine") != engine
            ):
                raise MatchExecutionError("GNU pass did not produce exact drop semantics")
        elif command == "accept":
            if (
                terminal_event.get("kind") != "resignation"
                or terminal_event.get("resignation_level") != pending.resignation_multiplier
            ):
                raise MatchExecutionError("GNU accept did not produce exact resignation semantics")
        elif checker_command and terminal_event.get("kind") != "ordinary_game_over":
            raise MatchExecutionError("GNU checker completion did not produce ordinary game-over semantics")
        match_complete = max(next_score[:2]) >= next_score[2]
        if match_complete:
            if consumed:
                raise MatchExecutionError("GNU consumed opening dice after the match was complete")
            expected_terminal_state = "resigned" if command == "accept" else "game_over"
            if (
                next_state.game_state != expected_terminal_state
                or next_state.decision_player is not None
                or next_state.dice is not None
            ):
                raise MatchExecutionError("GNU match completion retained an invalid turn/action state")
            completed_cube = (
                previous_cube[0], previous_cube[1], "none", None, None, None, None
            )
            if next_state.on_roll != state.on_roll or next_cube != completed_cube:
                raise MatchExecutionError("GNU match completion changed cube or turn ownership incorrectly")
            expected_board = expected_final_board if checker_command else previous_board
            if next_board != expected_board:
                raise MatchExecutionError("GNU match completion has the wrong resulting checker board")
        else:
            _validate_opening_transition(
                consumed,
                game_number + 1,
                next_position,
                engine_by_seat,
                expected_next_roll_seat,
                next_score[:2],
            )
        return

    if terminal_event is not None:
        raise MatchExecutionError("GNU reported a game completion without the exact score transition")
    if consumed and any(entry.get("prompt_type") == "opening" for entry in consumed):
        raise MatchExecutionError("GNU consumed opening dice without completing a game")
    if next_state.game_state != "playing" or next_score != previous_score:
        raise MatchExecutionError("GNU command left the active game or changed its score")

    if command == "roll":
        observed = _validate_consumed_checker_roll(
            consumed, game_number, physical_seat, engine_by_seat, expected_next_roll_seat, required=True
        )
        if next_state.on_roll != actor or next_state.decision_player != actor or tuple(next_state.dice or ()) != observed:
            raise MatchExecutionError("GNU roll attached the wrong dice, physical seat, or turn owner")
        if next_board != previous_board or next_cube != previous_cube:
            raise MatchExecutionError("GNU roll changed checker or cube state")
    elif command == "double":
        if consumed:
            raise MatchExecutionError("GNU double unexpectedly consumed manual dice")
        expected_pending = (cube_value, position.cube.owner, "double", actor, other, cube_value * 2, None)
        if (
            next_board != previous_board
            or next_state.on_roll != actor
            or next_state.decision_player != other
            or next_state.dice is not None
            or next_cube != expected_pending
            or expected_next_roll_seat != physical_seat
        ):
            raise MatchExecutionError("GNU double produced the wrong cube, action, or turn state")
    elif command == "take":
        observed = _validate_consumed_checker_roll(
            consumed, game_number, other_seat, engine_by_seat, expected_next_roll_seat, required=False
        )
        expected_pending = (cube_value * 2, actor, "none", None, None, None, None)
        if (
            next_board != previous_board
            or next_state.on_roll != other
            or next_state.decision_player != other
            or (tuple(next_state.dice) if next_state.dice is not None else None) != observed
            or next_cube != expected_pending
        ):
            raise MatchExecutionError("GNU take produced the wrong cube, action, dice, or turn state")
    elif checker_command:
        observed = _validate_consumed_checker_roll(
            consumed, game_number, other_seat, engine_by_seat, expected_next_roll_seat, required=False
        )
        if (
            next_board != expected_final_board
            or next_state.on_roll != other
            or next_state.decision_player != other
            or (tuple(next_state.dice) if next_state.dice is not None else None) != observed
            or next_cube != empty_current_cube
        ):
            raise MatchExecutionError("GNU checker command produced the wrong board, cube, action, dice, or turn state")
    else:
        raise MatchExecutionError(f"GNU {command} failed to complete the required game transition")


def _recommended_checker_notation(result: dict[str, Any]) -> str:
    try:
        decision = result["checker_decision"]
        move_id = decision["recommended_move_id"]
        candidates = decision["candidates"]
    except (KeyError, TypeError) as exc:
        raise MatchExecutionError("Engine Kit checker recommendation is malformed") from exc
    if not isinstance(candidates, list):
        raise MatchExecutionError("Engine Kit checker candidates are malformed")
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, Mapping) and item.get("move_id") == move_id
        ),
        None,
    )
    if candidate is None:
        raise MatchExecutionError("Engine Kit checker recommendation lacks a candidate")
    notation = candidate.get("notation") or candidate.get("raw_notation")
    if not notation:
        raise MatchExecutionError("Engine Kit checker recommendation lacks GNU-compatible notation")
    return str(notation)


def pending_double_response(cube_decision: Mapping[str, Any]) -> str:
    """Choose Take/Pass from responder semantics over normalized doubler equities."""
    if not isinstance(cube_decision, Mapping):
        raise MatchExecutionError("pending double decision is not normalized")
    actions = cube_decision.get("actions")
    if not isinstance(actions, list):
        raise MatchExecutionError("pending double lacks normalized cube actions")
    action_ids = {
        item.get("action_id") for item in actions if isinstance(item, Mapping)
    }
    if action_ids & {"double-beaver", "double-raccoon"}:
        raise MatchExecutionError("beaver/raccoon responses are forbidden in this campaign")
    equities: dict[str, float] = {}
    for action_id in ("double-take", "double-pass"):
        matches = [item for item in actions if isinstance(item, Mapping) and item.get("action_id") == action_id]
        if len(matches) != 1:
            raise MatchExecutionError(f"pending double requires exactly one {action_id} action")
        equity = matches[0].get("equity")
        if (
            not isinstance(equity, (int, float))
            or isinstance(equity, bool)
            or not math.isfinite(float(equity))
        ):
            raise MatchExecutionError(f"pending double {action_id} equity is not numeric")
        equities[action_id] = float(equity)
    if equities["double-take"] == equities["double-pass"]:
        raise MatchExecutionError("pending double responder equities are ambiguous")
    return "take" if equities["double-take"] < equities["double-pass"] else "pass"


def pre_roll_cube_action(cube_decision: Mapping[str, Any]) -> str:
    if not isinstance(cube_decision, Mapping):
        raise MatchExecutionError("pre-roll cube decision is not normalized")
    recommendation = cube_decision.get("recommended_action_id")
    if recommendation == "no-double":
        return "roll"
    if recommendation in {"double-take", "double-pass"}:
        return "double"
    raise MatchExecutionError("unsupported normal-match pre-roll cube recommendation")


class PairExecutor:
    def __init__(self, config: CampaignConfig, engine_kit: EngineKitSession):
        self.config = config
        self.engine_kit = engine_kit

    def run(self, identity: PairIdentity, workspace: Path) -> Path:
        attempt_workspace = Path(workspace).resolve(strict=True)
        if not attempt_workspace.is_dir() or len(attempt_workspace.parents) < 2:
            raise MatchExecutionError("pair workspace is not a durable directory anchor")
        durable_anchor = attempt_workspace.parents[1]
        _durably_link_existing_directory_hierarchy(durable_anchor, attempt_workspace)
        output = attempt_workspace / "pair-output"
        matches_root = output / "matches"
        for side in ("A", "B"):
            _durably_create_directory_hierarchy(attempt_workspace, matches_root / side)
        matches = []
        for side in ("A", "B"):
            matches.append(self._run_match(identity, side, matches_root / side, attempt_workspace))
        write_json(
            output / "execution_result.json",
            {
                "status": "complete",
                "pair_identity": identity.to_dict(),
                "matches": matches,
            },
        )
        return output

    @staticmethod
    def _analysis_context(
        identity: PairIdentity,
        side: str,
        game_number: int,
        physical_seat: str,
        engine: str,
        decision_type: str,
        gnuid: str,
        dice_values: tuple[int, int] | None,
        request_ordinal: int,
        decision_ordinal: int,
    ) -> dict[str, Any]:
        return {
            "campaign_id": identity.campaign_id,
            "pair_id": identity.pair_id,
            "pair_index": identity.pair_index,
            "pair_member": side,
            "match_side": side,
            "game_number": game_number,
            "request_ordinal": request_ordinal,
            "decision_ordinal": decision_ordinal,
            "physical_seat": physical_seat,
            "engine": engine,
            "decision_type": decision_type,
            "gnuid": gnuid,
            "dice": list(dice_values) if dice_values is not None else None,
        }

    @staticmethod
    def _raw_source_record(exc: BaseException) -> dict[str, Any] | None:
        raw_source = getattr(exc, "raw_source", None)
        if raw_source is None:
            return None
        if isinstance(raw_source, Mapping):
            return dict(raw_source)
        record = {
            key: getattr(raw_source, key)
            for key in ("inline", "content_sha256", "captured_at", "path")
            if getattr(raw_source, key, None) is not None
        }
        return record or {"representation": repr(raw_source)}

    @staticmethod
    def _returned_raw_evidence(result: Any) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []

        def visit(value: Any, path: str) -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    child = f"{path}.{key}" if path else str(key)
                    normalized = str(key).lower().replace("-", "_")
                    if normalized in {"raw_source", "raw_response"}:
                        evidence.append({"field": child, "value": item})
                    visit(item, child)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, f"{path}[{index}]")

        visit(result, "")
        return evidence

    def _persist_analysis_failure(
        self,
        match_root: Path,
        context: Mapping[str, Any],
        exc: BaseException,
        returned_result: Any = NO_RETURNED_RESULT,
    ) -> None:
        failure = {
            **context,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        }
        raw_source = self._raw_source_record(exc)
        if raw_source is not None:
            failure["exception_raw_source"] = raw_source
        if returned_result is not NO_RETURNED_RESULT:
            failure["returned_result"] = returned_result
            raw_evidence = self._returned_raw_evidence(returned_result)
            if raw_evidence:
                failure["returned_raw_evidence"] = raw_evidence
        write_json(match_root / "analysis_failure.json", failure)

    def _persist_policy_failure(
        self,
        match_root: Path,
        context: Mapping[str, Any],
        result: Any,
        exc: BaseException,
    ) -> None:
        try:
            self._persist_analysis_failure(match_root, context, exc, returned_result=result)
        except BaseException as persistence_exc:
            exc.add_note(
                "analysis failure evidence persistence also failed: "
                f"{type(persistence_exc).__name__}: {persistence_exc}"
            )

    def _analyze_with_forensics(
        self,
        identity: PairIdentity,
        side: str,
        match_root: Path,
        game_number: int,
        physical_seat: str,
        engine: str,
        decision_type: str,
        gnuid: str,
        dice_values: tuple[int, int] | None,
        request_ordinal: int,
        decision_ordinal: int,
    ) -> tuple[Any, dict[str, Any]]:
        request_record = self._analysis_context(
            identity, side, game_number, physical_seat, engine, decision_type, gnuid,
            dice_values, request_ordinal, decision_ordinal,
        )
        _append_jsonl_durable(match_root / "analysis_requests.jsonl", request_record)
        try:
            returned = self.engine_kit.analyze_raw(engine, decision_type, gnuid, dice_values, 900.0)
        except BaseException as exc:
            try:
                self._persist_analysis_failure(match_root, request_record, exc)
            except BaseException as persistence_exc:
                exc.add_note(
                    "analysis failure evidence persistence also failed: "
                    f"{type(persistence_exc).__name__}: {persistence_exc}"
                )
            raise
        result = analysis_result_forensics(returned.result)
        try:
            _append_jsonl_durable(
                match_root / "analysis_results.jsonl",
                {**request_record, "returned_result": result},
            )
        except BaseException as exc:
            try:
                self._persist_analysis_failure(match_root, request_record, exc, returned_result=result)
            except BaseException as persistence_exc:
                exc.add_note(
                    "analysis failure evidence persistence also failed: "
                    f"{type(persistence_exc).__name__}: {persistence_exc}"
                )
            raise
        try:
            validated_result = self.engine_kit.validate_analysis(returned)
        except BaseException as exc:
            try:
                self._persist_analysis_failure(match_root, request_record, exc, returned_result=result)
            except BaseException as persistence_exc:
                exc.add_note(
                    "analysis failure evidence persistence also failed: "
                    f"{type(persistence_exc).__name__}: {persistence_exc}"
                )
            raise
        return validated_result, request_record

    def _run_match(
        self,
        identity: PairIdentity,
        side: str,
        match_root: Path,
        durable_anchor: Path | None = None,
    ) -> dict[str, Any]:
        match_root = Path(match_root).resolve(strict=False)
        anchor = (
            Path(durable_anchor).resolve(strict=True)
            if durable_anchor is not None
            else match_root.parent
        )
        _durably_create_directory_hierarchy(anchor, match_root)
        if any(match_root.iterdir()):
            raise MatchExecutionError(f"match workspace is not empty: {match_root}")
        mapping = self.config.data["match"]["members"][side]
        engine_by_seat = {
            mapping["sage_physical_seat"]: "sage",
            mapping["gnu_physical_seat"]: "gnu",
        }
        dice = SeatDiceController(
            root=match_root / "dice",
            base_seed=identity.base_seed,
            side=side,
            roll_count=self.config.data["dice"]["roll_count_per_game_seat"],
            files_per_match=self.config.data["dice"]["files_per_match"],
            engine_by_seat=engine_by_seat,
            pair_id=identity.pair_id,
        )
        dice.prepare_files()
        board: GnuBoardProcess | None = None
        decision_path = match_root / "decisions.jsonl"
        request_path = match_root / "analysis_requests.jsonl"
        result_path = match_root / "analysis_results.jsonl"
        _create_empty_file_durable(decision_path)
        _create_empty_file_durable(request_path)
        _create_empty_file_durable(result_path)
        decisions = 0
        analysis_requests = 0
        game_action_ordinal = 0
        game_number = 1
        completed = False
        primary_error: BaseException | None = None
        dice_manifest: Path | None = None
        consumption: Path | None = None
        native_evidence: dict[str, Any] | None = None
        try:
            board = GnuBoardProcess(
                self.engine_kit.gnu_runtime.executable,
                self.engine_kit.gnu_runtime.environment(),
                dice,
                match_root / ".gnubg-home",
            )
            board.send("set pagination off")
            board.send("set rng manual")
            board.send("set player 0 human")
            board.send("set player 1 human")
            board.send(f"set player 0 name {engine_by_seat['O']}_seat_O")
            board.send(f"set player 1 name {engine_by_seat['X']}_seat_X")
            before_opening = len(dice.consumption)
            board.send("new match 7")
            board_text = board.send("show board")
            opening_position = self.engine_kit.position_from_gnuid(_gnuid(board_text))
            _validate_opening_transition(
                dice.consumption[before_opening:],
                1,
                opening_position,
                engine_by_seat,
                dice.expected_next_roll_seat,
                (0, 0),
            )
            with decision_path.open("a", encoding="utf-8", newline="") as evidence:
                while True:
                    gnuid = _gnuid(board_text)
                    position = self.engine_kit.position_from_gnuid(gnuid)
                    if max(position.score.player_0, position.score.player_1) >= 7:
                        break
                    decisions += 1
                    if decisions > self.config.data["bounds"]["max_decisions_per_match"]:
                        raise MatchExecutionError("match exceeded committed decision safety bound")
                    decision_player = position.state.decision_player or position.state.on_roll
                    physical_seat = _seat(decision_player)
                    engine = engine_by_seat[physical_seat]
                    pending = position.cube.pending_action.type
                    dice_values = position.state.dice
                    decision_type: str | None = None
                    analysis_dice: tuple[int, int] | None = None
                    if pending == "resignation":
                        command = "accept"
                        record = {"status": "board-rule", "action": "accept-resignation"}
                    elif pending == "double":
                        decision_type = "cube"
                        analysis_requests += 1
                        record, analysis_context = self._analyze_with_forensics(
                            identity, side, match_root, game_number, physical_seat,
                            engine, decision_type, gnuid, None,
                            analysis_requests,
                            decisions,
                        )
                        try:
                            command = pending_double_response(record["cube_decision"])
                        except BaseException as exc:
                            self._persist_policy_failure(match_root, analysis_context, record, exc)
                            raise
                    elif pending != "none":
                        raise MatchExecutionError(
                            f"unsupported pending action for normal seven-point match: {pending}"
                        )
                    elif dice_values is None:
                        decision_type = "cube"
                        analysis_requests += 1
                        record, analysis_context = self._analyze_with_forensics(
                            identity, side, match_root, game_number, physical_seat,
                            engine, decision_type, gnuid, None,
                            analysis_requests,
                            decisions,
                        )
                        try:
                            command = pre_roll_cube_action(record["cube_decision"])
                        except BaseException as exc:
                            self._persist_policy_failure(match_root, analysis_context, record, exc)
                            raise
                    else:
                        decision_type = "checker"
                        analysis_dice = tuple(int(value) for value in dice_values)
                        analysis_requests += 1
                        record, analysis_context = self._analyze_with_forensics(
                            identity, side, match_root, game_number, physical_seat,
                            engine, decision_type, gnuid, analysis_dice,
                            analysis_requests,
                            decisions,
                        )
                        try:
                            command = _recommended_checker_notation(record)
                        except BaseException as exc:
                            self._persist_policy_failure(match_root, analysis_context, record, exc)
                            raise
                        dice.prepare_after_turn(game_number, physical_seat)
                    decision_evidence = {
                        "campaign_id": identity.campaign_id,
                        "pair_id": identity.pair_id,
                        "pair_index": identity.pair_index,
                        "pair_member": side,
                        "match_side": side,
                        "record_ordinal": decisions,
                        "decision_ordinal": decisions,
                        "action_ordinal": None,
                        "game_number": game_number,
                        "physical_seat": physical_seat,
                        "engine": engine,
                        "gnuid": gnuid,
                        "decision_type": decision_type or "board-rule",
                        "analysis_dice": list(analysis_dice) if analysis_dice is not None else None,
                        "command": command,
                        "engine_kit_result": record,
                    }
                    if command != "roll" and command != "accept":
                        game_action_ordinal += 1
                        decision_evidence["action_ordinal"] = game_action_ordinal
                    before_consumption = len(dice.consumption)
                    previous_score = (int(position.score.player_0), int(position.score.player_1))
                    output = board.send(command, timeout_seconds=120.0)
                    consumed = dice.consumption[before_consumption:]
                    next_board_text = board.send("show board")
                    next_gnuid = _gnuid(next_board_text)
                    if next_gnuid == gnuid:
                        raise MatchExecutionError(f"GNU command {command!r} did not change board state")
                    next_position = self.engine_kit.position_from_gnuid(next_gnuid)
                    next_score = (int(next_position.score.player_0), int(next_position.score.player_1))
                    score_changed = next_score != previous_score
                    terminal_event = _parse_terminal_event(output)
                    _validate_command_transition(
                        command,
                        position,
                        next_position,
                        consumed,
                        game_number,
                        physical_seat,
                        engine,
                        engine_by_seat,
                        dice.expected_next_roll_seat,
                        terminal_event,
                    )
                    opening_consumed = any(entry.get("prompt_type") == "opening" for entry in consumed)
                    subsequent_opening = None
                    if terminal_event is not None and max(next_score) < 7:
                        subsequent_opening = {
                            "game_number": game_number + 1,
                            "gnuid": next_gnuid,
                            "score": list(next_score),
                            "on_roll_physical_seat": _seat(next_position.state.on_roll),
                            "decision_physical_seat": _seat(next_position.state.decision_player),
                            "dice": list(next_position.state.dice),
                        }
                    decision_evidence["transition_evidence"] = {
                        "command_type": (
                            "pass" if command == "pass" else
                            "accepted_resignation" if command == "accept" else
                            "checker" if command not in {"roll", "double", "take"} else command
                        ),
                        "acting_physical_seat": physical_seat,
                        "acting_engine": engine,
                        "pre_command": {"gnuid": gnuid, "score": list(previous_score)},
                        "terminal_event": terminal_event,
                        "post_command": {"gnuid": next_gnuid, "score": list(next_score)},
                        "game_number": game_number,
                        "subsequent_opening_state": subsequent_opening,
                    }
                    evidence.write(
                        json.dumps(decision_evidence, sort_keys=True, separators=(",", ":")) + "\n"
                    )
                    evidence.flush()
                    os.fsync(evidence.fileno())
                    if score_changed:
                        if max(next_score) >= 7:
                            if opening_consumed:
                                raise MatchExecutionError("GNU consumed an opening after the match was complete")
                        else:
                            next_game_number = game_number + 1
                            _validate_opening_transition(
                                consumed,
                                next_game_number,
                                next_position,
                                engine_by_seat,
                                dice.expected_next_roll_seat,
                                next_score,
                            )
                            game_number = next_game_number
                            game_action_ordinal = 0
                    elif opening_consumed:
                        raise MatchExecutionError("GNU consumed an opening without a completed-game transition")
                    board_text = next_board_text
            native = match_root / "native"
            native.mkdir()
            board.send(f"save match {native / 'match.sgf'}")
            board.send(f"export match text {native / 'match.txt'}")
            native_evidence = _validate_native_outputs(
                native / "match.sgf", native / "match.txt", engine_by_seat,
                _frozen_gnu_sgf_application(self.config),
            )
            write_json(native / "board_transcript.json", board.transcript)
            completed = True
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            try:
                dice_manifest, consumption = dice.write_evidence()
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                if board is not None:
                    write_json(match_root / "board_transcript.partial.json", board.transcript)
            except BaseException as exc:
                cleanup_errors.append(exc)
            if board is not None:
                try:
                    board.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                if primary_error is not None:
                    for cleanup_error in cleanup_errors:
                        primary_error.add_note(
                            f"cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}"
                        )
                else:
                    for cleanup_error in cleanup_errors[1:]:
                        cleanup_errors[0].add_note(
                            f"additional cleanup failure: {type(cleanup_error).__name__}: {cleanup_error}"
                        )
                    raise cleanup_errors[0]
        if completed:
            (match_root / "board_transcript.partial.json").unlink(missing_ok=True)
        if dice_manifest is None or consumption is None or native_evidence is None:
            raise MatchExecutionError("match dice evidence was not persisted")
        required = (match_root / "native" / "match.sgf", match_root / "native" / "match.txt", decision_path)
        if not all(path.is_file() for path in required):
            raise MatchExecutionError(f"match {side} did not produce all native evidence")
        manifest = {
            "side": side,
            "pair_member": side,
            "engine_by_physical_seat": engine_by_seat,
            "namespace_seed": dice.seed,
            "dice_manifest": str(dice_manifest.relative_to(match_root)),
            "dice_consumption": str(consumption.relative_to(match_root)),
            "candidate_actual_depth_evidence": decision_path.name,
            "analysis_request_evidence": request_path.name,
            "analysis_result_evidence": result_path.name,
            "native_outputs": [str(path.relative_to(match_root)) for path in required[:2]],
            "native_evidence": native_evidence,
            "output_sha256": {
                str(path.relative_to(match_root)): sha256_file(path)
                for path in (*required, dice_manifest, consumption, request_path, result_path)
            },
        }
        write_json(match_root / "match_manifest.json", manifest)
        _validate_complete_native_evidence(
            match_root, engine_by_seat, _frozen_gnu_sgf_application(self.config)
        )
        return manifest
