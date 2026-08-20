"""Two-human GNU board referee with decisions delegated to Engine Kit."""
from __future__ import annotations

import errno
import json
import math
import os
import pty
import shutil
import re
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
        except Exception:
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
        shutil.rmtree(self.isolated_home, ignore_errors=True)

    def _read_until_prompt(self, command: str, timeout_seconds: float = 60.0) -> str:
        deadline = time.monotonic() + timeout_seconds
        buffer = b""
        search_from = 0
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
                context = ANSI_RE.sub("", buffer[: dice_index + len(MANUAL_DICE)].decode("utf-8", "replace"))
                die1, die2 = self.dice.dice_for_prompt(context)
                os.write(self.master_fd, f"{die1} {die2}\n".encode("ascii"))
                search_from = dice_index + len(MANUAL_DICE)
                continue
            if PROMPT_RE.search(buffer):
                text = ANSI_RE.sub("", buffer.decode("utf-8", "replace")).replace("\r", "")
                self.transcript.append({"command": command, "output": text})
                return text
        raise MatchExecutionError(f"GNU board process timed out during {command!r}")

    def send(self, command: str, timeout_seconds: float = 60.0) -> str:
        os.write(self.master_fd, (command + "\n").encode("utf-8"))
        return self._read_until_prompt(command, timeout_seconds)

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
        board = GnuBoardProcess(
            self.engine_kit.gnu_runtime.executable,
            self.engine_kit.gnu_runtime.environment(),
            dice,
            match_root / ".gnubg-home",
        )
        decision_path = match_root / "decisions.jsonl"
        decisions = 0
        game_number = 1
        try:
            board.send("set pagination off")
            board.send("set rng manual")
            board.send("set player 0 human")
            board.send("set player 1 human")
            board.send(f"set player 0 name {engine_by_seat['O']}_seat_O")
            board.send(f"set player 1 name {engine_by_seat['X']}_seat_X")
            board.send("new match 7")
            with decision_path.open("w", encoding="utf-8", newline="") as evidence:
                while True:
                    board_text = board.send("show board")
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
                    if pending == "resignation":
                        command = "accept"
                        record = {"status": "board-rule", "action": "accept-resignation"}
                    elif pending == "double":
                        record = self.engine_kit.analyze(engine, "cube", gnuid, None, 900.0)
                        command = pending_double_response(record["cube_decision"])
                    elif pending != "none":
                        raise MatchExecutionError(
                            f"unsupported pending action for normal seven-point match: {pending}"
                        )
                    elif dice_values is None:
                        record = self.engine_kit.analyze(engine, "cube", gnuid, None, 900.0)
                        command = pre_roll_cube_action(record["cube_decision"])
                    else:
                        checker_dice = tuple(int(value) for value in dice_values)
                        record = self.engine_kit.analyze(engine, "checker", gnuid, checker_dice, 900.0)
                        command = _recommended_checker_notation(record)
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
                    output = board.send(command, timeout_seconds=120.0)
                    if "wins " in output.lower() and " point" in output.lower():
                        game_number = dice.current_game_number
            native = match_root / "native"
            native.mkdir()
            board.send(f"save match {native / 'match.sgf'}")
            board.send(f"export match text {native / 'match.txt'}")
            write_json(native / "board_transcript.json", board.transcript)
        finally:
            try:
                dice_manifest, consumption = dice.write_evidence()
            finally:
                board.close()
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
            "native_outputs": [str(path.relative_to(match_root)) for path in required[:2]],
            "output_sha256": {
                str(path.relative_to(match_root)): sha256_file(path)
                for path in (*required, dice_manifest, consumption)
            },
        }
        write_json(match_root / "match_manifest.json", manifest)
        return manifest
