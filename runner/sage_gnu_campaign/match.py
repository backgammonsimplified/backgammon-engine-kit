"""Two-human GNU board referee with decisions delegated to Engine Kit."""
from __future__ import annotations

import errno
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
from typing import Any, Mapping

from .config import CampaignConfig
from .dice import SeatDiceController
from .engine_kit import EngineKitSession, analysis_result_forensics
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
GAME_WIN_RE = re.compile(r"(?im)\bwins\b[^\r\n]{0,80}\bpoints?\b")
TEXT_MATCH_RE = re.compile(r"(?im)\b(?:\d+\s+point\s+match|match\s+to\s+\d+\s+points?)\b")
TEXT_GAME_RE = re.compile(r"(?im)^\s*game\s+\d+\b")
TEXT_PLAYER_SCORE_RE = re.compile(r"(?im)\b(sage|gnu)_seat_([OX])\s*:\s*\d+\b")
NO_RETURNED_RESULT = object()


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
) -> None:
    try:
        sgf = sgf_path.read_text(encoding="utf-8-sig")
        exported = text_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise MatchExecutionError("GNU native outputs are absent or unreadable") from exc
    stripped_sgf = sgf.strip()
    if (
        len(stripped_sgf) < 24
        or not stripped_sgf.startswith("(;")
        or not stripped_sgf.endswith(")")
        or not _sgf_delimiters_are_balanced(stripped_sgf)
        or "FF[4]" not in stripped_sgf
        or "GM[6]" not in stripped_sgf
        or "AP[GNU Backgammon:" not in stripped_sgf
        or "MI[length:7]" not in stripped_sgf
    ):
        raise MatchExecutionError("GNU saved SGF is empty or structurally invalid")
    player_scores = TEXT_PLAYER_SCORE_RE.findall(exported)
    expected_player_scores = sorted(
        (engine, seat) for seat, engine in expected_engine_by_seat.items()
    )
    if (
        len(exported.strip()) < 24
        or "\x00" in exported
        or TEXT_MATCH_RE.search(exported) is None
        or TEXT_GAME_RE.search(exported) is None
        or set(expected_engine_by_seat) != {"O", "X"}
        or set(expected_engine_by_seat.values()) != {"sage", "gnu"}
        or sorted(player_scores) != expected_player_scores
    ):
        raise MatchExecutionError("GNU exported match text is empty or structurally invalid")


def _sgf_delimiters_are_balanced(value: str) -> bool:
    tree_depth = 0
    in_property = False
    escaped = False
    for character in value:
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
        elif character == "]":
            return False
        elif character == "(":
            tree_depth += 1
        elif character == ")":
            tree_depth -= 1
            if tree_depth < 0:
                return False
    return tree_depth == 0 and not in_property and not escaped


def _validate_opening_transition(
    consumed: list[dict[str, Any]],
    game_number: int,
    position: Any,
    engine_by_seat: Mapping[str, str],
    expected_next_roll_seat: str | None,
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
    try:
        on_roll = _seat(position.state.on_roll)
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
    expected_dice = (int(final_o["die1"]), int(final_x["die1"]))
    if on_roll != winner:
        raise MatchExecutionError("GNU opening winner differs from the physical seat on roll")
    if len(observed_dice) != 2 or sorted(observed_dice) != sorted(expected_dice):
        raise MatchExecutionError("GNU opening board dice differ from the consumed opening dice")
    if expected_next_roll_seat != loser:
        raise MatchExecutionError("GNU opening dice were misapplied to the physical-seat stream")
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
        self.isolated_home.mkdir(parents=False, exist_ok=False)
        env = _board_environment(environment, self.isolated_home)
        self.master_fd = -1
        try:
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
            finally:
                os.close(slave_fd)
            self.dice = dice
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.master_fd, selectors.EVENT_READ)
            self.transcript: list[dict[str, str]] = []
            self._read_until_prompt("<startup>")
        except BaseException:
            self._cleanup(terminate=True)
            raise

    def _cleanup(self, *, terminate: bool) -> None:
        process = getattr(self, "process", None)
        if process is not None and terminate and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        selector = getattr(self, "selector", None)
        if selector is not None:
            selector.close()
        if getattr(self, "master_fd", -1) >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1
        if self.isolated_home.exists():
            shutil.rmtree(self.isolated_home)
        if self.isolated_home.exists():
            raise MatchExecutionError("GNU isolated HOME survived cleanup")

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
                    if bars[actor] <= 0:
                        raise MatchExecutionError("GNU checker notation moves an absent bar checker")
                    bars[actor] -= 1
                elif source == "off":
                    raise MatchExecutionError("GNU checker notation moves a borne-off checker")
                else:
                    source_index = int(source) - 1
                    if points[actor][source_index] <= 0:
                        raise MatchExecutionError("GNU checker notation moves an absent checker")
                    points[actor][source_index] -= 1
                if destination == "bar":
                    raise MatchExecutionError("GNU checker notation cannot move to the bar")
                if destination == "off":
                    offs[actor] += 1
                    continue
                destination_index = int(destination) - 1
                opponent_index = 23 - destination_index
                if points[opponent][opponent_index] > 1:
                    raise MatchExecutionError("GNU checker notation lands on a blocked point")
                if points[opponent][opponent_index] == 1:
                    points[opponent][opponent_index] = 0
                    bars[opponent] += 1
                points[actor][destination_index] += 1
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
    reported_completion: bool,
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
    score_delta = (next_score[0] - previous_score[0], next_score[1] - previous_score[1])
    score_changed = score_delta != (0, 0)
    if score_changed != reported_completion:
        raise MatchExecutionError("GNU completed-game output/state transition is missing or misparsed")

    checker_command = command not in {"roll", "double", "take", "pass", "accept"}
    winner: str | None = None
    expected_points: int | None = None
    expected_final_board: tuple[Any, ...] | None = None
    if checker_command:
        if pending.type != "none" or state.on_roll != actor or state.dice is None:
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
        elif score_changed:
            raise MatchExecutionError("GNU checker command awarded points before bearing off all checkers")
    elif command in {"roll", "double"}:
        if pending.type != "none" or state.on_roll != actor or state.dice is not None:
            raise MatchExecutionError(f"GNU {command} precondition is semantically invalid")
        if command == "double" and position.cube.owner not in {"center", actor}:
            raise MatchExecutionError("GNU double precondition has the wrong cube owner")
    elif command in {"take", "pass"}:
        if (
            pending.type != "double"
            or pending.responder != actor
            or pending.offerer != other
            or state.on_roll != other
            or state.dice is not None
            or pending.offered_cube_value != cube_value * 2
            or position.cube.owner not in {"center", other}
        ):
            raise MatchExecutionError(f"GNU {command} precondition is semantically invalid")
        winner = other if command == "pass" else None
        expected_points = cube_value if command == "pass" else None
    elif command == "accept":
        multiplier = pending.resignation_multiplier
        if (
            pending.type != "resignation"
            or pending.responder != actor
            or pending.offerer != other
            or state.on_roll != other
            or type(multiplier) is not int
            or not 1 <= multiplier <= 3
            or pending.offered_cube_value is not None
        ):
            raise MatchExecutionError("GNU resignation precondition is semantically invalid")
        winner = actor
        expected_points = cube_value * multiplier
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
        match_complete = max(next_score[:2]) >= next_score[2]
        if match_complete:
            if consumed:
                raise MatchExecutionError("GNU consumed opening dice after the match was complete")
            if next_state.game_state not in {"game_over", "resigned"} or next_state.decision_player is not None or next_state.dice is not None:
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
            if not _is_starting_board(next_position):
                raise MatchExecutionError("GNU next game did not reset to the exact starting checker board")
            if next_position.cube.value != 1 or next_position.cube.owner != "center" or next_position.cube.pending_action.type != "none":
                raise MatchExecutionError("GNU next game did not reset cube ownership/value/action state")
        return

    if reported_completion:
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
            or next_position.cube.value != cube_value
            or next_position.cube.owner != position.cube.owner
            or next_position.cube.pending_action.type != "none"
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
    ) -> dict[str, Any]:
        return {
            "campaign_id": identity.campaign_id,
            "pair_id": identity.pair_id,
            "pair_index": identity.pair_index,
            "pair_member": side,
            "match_side": side,
            "game_number": game_number,
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
    ) -> tuple[Any, dict[str, Any]]:
        request_record = self._analysis_context(
            identity, side, game_number, physical_seat, engine, decision_type, gnuid, dice_values
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
        )
        dice.prepare_files()
        board: GnuBoardProcess | None = None
        decision_path = match_root / "decisions.jsonl"
        request_path = match_root / "analysis_requests.jsonl"
        result_path = match_root / "analysis_results.jsonl"
        _create_empty_file_durable(request_path)
        _create_empty_file_durable(result_path)
        decisions = 0
        game_number = 1
        completed = False
        primary_error: BaseException | None = None
        dice_manifest: Path | None = None
        consumption: Path | None = None
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
            )
            with decision_path.open("w", encoding="utf-8", newline="") as evidence:
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
                        record, analysis_context = self._analyze_with_forensics(
                            identity, side, match_root, game_number, physical_seat,
                            engine, decision_type, gnuid, None,
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
                        record, analysis_context = self._analyze_with_forensics(
                            identity, side, match_root, game_number, physical_seat,
                            engine, decision_type, gnuid, None,
                        )
                        try:
                            command = pre_roll_cube_action(record["cube_decision"])
                        except BaseException as exc:
                            self._persist_policy_failure(match_root, analysis_context, record, exc)
                            raise
                    else:
                        decision_type = "checker"
                        analysis_dice = tuple(int(value) for value in dice_values)
                        record, analysis_context = self._analyze_with_forensics(
                            identity, side, match_root, game_number, physical_seat,
                            engine, decision_type, gnuid, analysis_dice,
                        )
                        try:
                            command = _recommended_checker_notation(record)
                        except BaseException as exc:
                            self._persist_policy_failure(match_root, analysis_context, record, exc)
                            raise
                        dice.prepare_after_turn(game_number, physical_seat)
                    evidence.write(
                        json.dumps(
                            {
                                "campaign_id": identity.campaign_id,
                                "pair_id": identity.pair_id,
                                "pair_index": identity.pair_index,
                                "pair_member": side,
                                "match_side": side,
                                "game_number": game_number,
                                "physical_seat": physical_seat,
                                "engine": engine,
                                "gnuid": gnuid,
                                "command": command,
                                "engine_kit_result": record,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
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
                    reported_completion = GAME_WIN_RE.search(output) is not None
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
                        reported_completion,
                    )
                    opening_consumed = any(entry.get("prompt_type") == "opening" for entry in consumed)
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
                            )
                            game_number = next_game_number
                    elif opening_consumed:
                        raise MatchExecutionError("GNU consumed an opening without a completed-game transition")
                    board_text = next_board_text
            native = match_root / "native"
            native.mkdir()
            board.send(f"save match {native / 'match.sgf'}")
            board.send(f"export match text {native / 'match.txt'}")
            _validate_native_outputs(native / "match.sgf", native / "match.txt", engine_by_seat)
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
        if dice_manifest is None or consumption is None:
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
            "output_sha256": {
                str(path.relative_to(match_root)): sha256_file(path)
                for path in (*required, dice_manifest, consumption, request_path, result_path)
            },
        }
        write_json(match_root / "match_manifest.json", manifest)
        return manifest
