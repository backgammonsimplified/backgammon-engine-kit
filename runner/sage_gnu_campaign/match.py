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
from .engine_kit import EngineKitSession
from .identity import PairIdentity
from .manifests import sha256_file, write_json


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


def _validate_native_outputs(sgf_path: Path, text_path: Path) -> None:
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
    player_scores = set(TEXT_PLAYER_SCORE_RE.findall(exported))
    if (
        len(exported.strip()) < 24
        or "\x00" in exported
        or TEXT_MATCH_RE.search(exported) is None
        or TEXT_GAME_RE.search(exported) is None
        or {engine for engine, _ in player_scores} != {"sage", "gnu"}
        or {seat for _, seat in player_scores} != {"O", "X"}
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
        output = Path(workspace) / "pair-output"
        output.mkdir(parents=True, exist_ok=False)
        matches = []
        for side in ("A", "B"):
            matches.append(self._run_match(identity, side, output / "matches" / side))
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
            result = self.engine_kit.analyze(engine, decision_type, gnuid, dice_values, 900.0)
        except BaseException as exc:
            try:
                self._persist_analysis_failure(match_root, request_record, exc)
            except BaseException as persistence_exc:
                exc.add_note(
                    "analysis failure evidence persistence also failed: "
                    f"{type(persistence_exc).__name__}: {persistence_exc}"
                )
            raise
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
        return result, request_record

    def _run_match(self, identity: PairIdentity, side: str, match_root: Path) -> dict[str, Any]:
        match_root.mkdir(parents=True, exist_ok=False)
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
        request_path.touch(exist_ok=False)
        result_path.touch(exist_ok=False)
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
                    if command == "roll":
                        if (
                            len(consumed) != 1
                            or consumed[0].get("prompt_type") != "checker"
                            or consumed[0].get("physical_seat") != physical_seat
                            or consumed[0].get("engine") != engine
                        ):
                            raise MatchExecutionError("GNU roll did not consume exactly one expected physical-seat dice record")
                    next_board_text = board.send("show board")
                    next_gnuid = _gnuid(next_board_text)
                    if next_gnuid == gnuid:
                        raise MatchExecutionError(f"GNU command {command!r} did not change board state")
                    next_position = self.engine_kit.position_from_gnuid(next_gnuid)
                    if command == "roll":
                        observed = next_position.state.dice
                        expected = (int(consumed[0]["die1"]), int(consumed[0]["die2"]))
                        if observed is None or sorted(int(v) for v in observed) != sorted(expected):
                            raise MatchExecutionError("GNU board dice differ from the consumed physical-seat stream")
                    next_score = (int(next_position.score.player_0), int(next_position.score.player_1))
                    score_changed = next_score != previous_score
                    reported_completion = GAME_WIN_RE.search(output) is not None
                    opening_consumed = any(entry.get("prompt_type") == "opening" for entry in consumed)
                    if score_changed != reported_completion:
                        raise MatchExecutionError("GNU completed-game output/state transition is missing or misparsed")
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
            _validate_native_outputs(native / "match.sgf", native / "match.txt")
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
