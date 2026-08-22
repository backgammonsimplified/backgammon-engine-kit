from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.engine_kit import ReturnedAnalysis
from runner.sage_gnu_campaign.identity import pair_identity
from runner.sage_gnu_campaign.manifests import write_json
from runner.sage_gnu_campaign.match import (
    MatchExecutionError,
    PairExecutor,
    _board_environment,
    _raise_on_gnu_error,
    _validate_native_outputs,
    _validate_opening_transition,
    _recommended_checker_notation,
    pending_double_response,
    pre_roll_cube_action,
)


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"


def test_board_environment_overrides_engine_kit_dev_null_home(tmp_path: Path) -> None:
    isolated = tmp_path / "gnubg-home"
    env = _board_environment(
        {"HOME": "/dev/null", "LANG": "C", "LC_ALL": "C", "OMP_NUM_THREADS": "1"},
        isolated,
    )
    assert env["HOME"] == str(isolated.resolve())
    assert env["HOME"] != "/dev/null"
    assert env["LANG"] == "C"
    assert env["LC_ALL"] == "C"
    assert env["OMP_NUM_THREADS"] == "1"


def cube_decision(take: object, passed: object, recommendation: str = "no-double") -> dict:
    return {
        "recommended_action_id": recommendation,
        "actions": [
            {"action_id": "no-double", "equity": 0.1},
            {"action_id": "double-take", "equity": take},
            {"action_id": "double-pass", "equity": passed},
        ],
    }


def test_pending_double_uses_responder_equities_not_overall_recommendation() -> None:
    assert pending_double_response(cube_decision(0.2, 0.8, "no-double")) == "take"
    assert pending_double_response(cube_decision(0.8, 0.2, "double-take")) == "pass"


@pytest.mark.parametrize("take,passed", [(None, 0.2), ("bad", 0.2), (0.2, float("nan"))])
def test_pending_double_missing_or_non_numeric_equity_fails_closed(take: object, passed: object) -> None:
    with pytest.raises(MatchExecutionError, match="equity"):
        pending_double_response(cube_decision(take, passed))


def test_pending_double_missing_or_ambiguous_actions_fail_closed() -> None:
    with pytest.raises(MatchExecutionError, match="exactly one"):
        pending_double_response({"actions": [{"action_id": "double-take", "equity": 0.2}]})
    with pytest.raises(MatchExecutionError, match="ambiguous"):
        pending_double_response(cube_decision(0.2, 0.2))
    beaver = cube_decision(0.2, 0.8)
    beaver["actions"].append({"action_id": "double-beaver", "equity": 0.1})
    with pytest.raises(MatchExecutionError, match="beaver/raccoon"):
        pending_double_response(beaver)


def test_pre_roll_cube_and_checker_policy_is_explicit() -> None:
    assert pre_roll_cube_action(cube_decision(0.2, 0.8, "no-double")) == "roll"
    assert pre_roll_cube_action(cube_decision(0.2, 0.8, "double-take")) == "double"
    assert pre_roll_cube_action(cube_decision(0.2, 0.8, "double-pass")) == "double"
    with pytest.raises(MatchExecutionError):
        pre_roll_cube_action(cube_decision(0.2, 0.8, "double-beaver"))
    assert (
        _recommended_checker_notation(
            {
                "checker_decision": {
                    "recommended_move_id": "m1",
                    "candidates": [{"move_id": "m1", "notation": "13/8"}],
                }
            }
        )
        == "13/8"
    )


class FakeDice:
    def __init__(self, root: Path, **_: object):
        self.root = root
        self.seed = "fake-seed"
        self.current_game_number = 1
        self.expected_next_roll_seat = None
        self.consumption: list[dict[str, object]] = []

    def prepare_files(self) -> None:
        self.root.mkdir(parents=True)

    def prepare_after_turn(self, game_number: int, physical_seat: str) -> None:
        assert game_number in {1, 2}
        assert physical_seat in {"O", "X"}
        self.expected_next_roll_seat = "X" if physical_seat == "O" else "O"

    def write_evidence(self) -> tuple[Path, Path]:
        manifest = self.root / "seat_dice_manifest.json"
        consumption = self.root / "seat_dice_consumption.json"
        write_json(manifest, {"status": "fake"})
        write_json(consumption, {"status": "fake"})
        return manifest, consumption


class FakeBoard:
    last: "FakeBoard | None" = None

    def __init__(self, *_: object):
        self.dice = _[2]
        self.commands: list[str] = []
        self.transcript: list[dict[str, str]] = []
        self.board_index = 0
        self.move_count = 0
        FakeBoard.last = self

    def send(self, command: str, timeout_seconds: float = 60.0) -> str:
        del timeout_seconds
        self.commands.append(command)
        if command == "new match 7":
            self.dice.consumption.extend([
                {"prompt_type": "opening", "game_number": 1, "roll_index": 1, "physical_seat": "O", "engine": "sage", "die1": 3, "die2": None},
                {"prompt_type": "opening", "game_number": 1, "roll_index": 1, "physical_seat": "X", "engine": "gnu", "die1": 1, "die2": None},
            ])
            self.dice.expected_next_roll_seat = "X"
        if command == "roll":
            seat = self.dice.expected_next_roll_seat or "X"
            self.dice.consumption.append(
                {"prompt_type": "checker", "game_number": self.dice.current_game_number, "physical_seat": seat, "engine": "gnu" if seat == "X" else "sage", "die1": 3, "die2": 1}
            )
        if command == "13/8":
            self.move_count += 1
            if self.move_count == 1:
                self.dice.current_game_number = 2
                self.dice.consumption.extend([
                    {"prompt_type": "opening", "game_number": 2, "roll_index": 1, "physical_seat": "O", "engine": "sage", "die1": 2, "die2": None},
                    {"prompt_type": "opening", "game_number": 2, "roll_index": 1, "physical_seat": "X", "engine": "gnu", "die1": 2, "die2": None},
                    {"prompt_type": "opening", "game_number": 2, "roll_index": 2, "physical_seat": "O", "engine": "sage", "die1": 1, "die2": None},
                    {"prompt_type": "opening", "game_number": 2, "roll_index": 2, "physical_seat": "X", "engine": "gnu", "die1": 4, "die2": None},
                ])
                self.dice.expected_next_roll_seat = "O"
                return "sage wins 1 point"
        if command == "show board":
            self.board_index += 1
            return f"Position ID: P{self.board_index}\nMatch ID: M{self.board_index}\n"
        if command.startswith("save match "):
            path = Path(command.removeprefix("save match "))
            path.write_text("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7][game:0])\n", encoding="utf-8")
        if command.startswith("export match text "):
            path = Path(command.removeprefix("export match text "))
            path.write_text("7 point match\n\n Game 1\n sage_seat_O : 0  gnu_seat_X : 0\n", encoding="utf-8")
        if command == "accept":
            return "sage wins 6 points"
        return "ok"

    def close(self) -> None:
        pass


def position(score: int, player: str, pending: str, dice: tuple[int, int] | None) -> object:
    return SimpleNamespace(
        score=SimpleNamespace(player_0=score, player_1=0),
        state=SimpleNamespace(decision_player=player, on_roll=player, dice=dice),
        cube=SimpleNamespace(pending_action=SimpleNamespace(type=pending)),
    )


class FakeEngineKit:
    def __init__(self) -> None:
        self.gnu_runtime = SimpleNamespace(executable=Path("/fake/gnubg"), environment=lambda: {})
        self.positions = iter(
            [
                position(0, "player_0", "none", (3, 1)),
                position(1, "player_1", "none", (4, 1)),
                position(1, "player_0", "none", None),
                position(1, "player_0", "none", (3, 1)),
                position(1, "player_1", "double", None),
                position(1, "player_0", "resignation", None),
                position(7, "player_0", "none", None),
            ]
        )
        self.analysis_calls: list[tuple[str, str]] = []

    def position_from_gnuid(self, gnuid: str) -> object:
        assert gnuid.startswith("P")
        if not hasattr(self, "_position_cache"):
            self._position_cache = list(self.positions)
        index = int(gnuid.split(":", 1)[0].removeprefix("P")) - 1
        return self._position_cache[index]

    def analyze(
        self,
        engine: str,
        decision_type: str,
        gnuid: str,
        dice: tuple[int, int] | None,
        timeout_seconds: float,
    ) -> dict:
        del gnuid, dice, timeout_seconds
        self.analysis_calls.append((engine, decision_type))
        if decision_type == "checker":
            return {
                "checker_decision": {
                    "recommended_move_id": "m1",
                    "candidates": [{"move_id": "m1", "notation": "13/8"}],
                }
            }
        if len([call for call in self.analysis_calls if call[1] == "cube"]) == 1:
            return {"cube_decision": cube_decision(0.2, 0.8, "no-double")}
        return {"cube_decision": cube_decision(0.2, 0.8, "no-double")}

    def analyze_raw(self, engine, decision_type, gnuid, dice, timeout_seconds):
        return ReturnedAnalysis(
            request=None,
            result=self.analyze(engine, decision_type, gnuid, dice, timeout_seconds),
        )

    @staticmethod
    def validate_analysis(returned):
        return returned.result


def test_run_match_simulates_all_normal_policy_paths_without_board_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", FakeBoard)
    config = load_campaign_config(CONFIG)
    engine_kit = FakeEngineKit()
    manifest = PairExecutor(config, engine_kit)._run_match(
        pair_identity(config, 1),
        "A",
        tmp_path / "match-A",
    )

    assert manifest["side"] == "A"
    assert engine_kit.analysis_calls == [
        ("sage", "checker"),
        ("gnu", "checker"),
        ("sage", "cube"),
        ("sage", "checker"),
        ("gnu", "cube"),
    ]
    assert FakeBoard.last is not None
    assert [command for command in FakeBoard.last.commands if command in {"roll", "double", "take", "pass", "13/8", "accept"}] == [
        "13/8",
        "13/8",
        "roll",
        "13/8",
        "take",
        "accept",
    ]
    assert not any(
        command == "hint" or command.startswith("hint ") or command == "show evaluation"
        for command in FakeBoard.last.commands
    )
    records = [json.loads(line) for line in (tmp_path / "match-A/decisions.jsonl").read_text().splitlines()]
    resignation = next(record for record in records if record["command"] == "accept")
    assert resignation["engine_kit_result"] == {
        "status": "board-rule",
        "action": "accept-resignation",
    }


def test_malformed_returned_result_is_persisted_and_cleanup_cannot_mask_policy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    class MalformedEngineKit(FakeEngineKit):
        def __init__(self) -> None:
            self.gnu_runtime = SimpleNamespace(executable=Path("/fake/gnubg"), environment=lambda: {})
            self.positions = iter([position(0, "player_0", "none", (3, 1))])
            self.analysis_calls = []

        def analyze(self, engine, decision_type, gnuid, dice, timeout_seconds):
            del engine, decision_type, gnuid, dice, timeout_seconds
            return {
                "checker_decision": {"recommended_move_id": "missing"},
                "raw_source": {"inline": "raw engine response", "content_sha256": "a" * 64},
            }

    class CleanupFailingBoard(FakeBoard):
        def close(self) -> None:
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", CleanupFailingBoard)
    match_root = tmp_path / "match-A"
    with pytest.raises(MatchExecutionError, match="checker recommendation is malformed") as caught:
        PairExecutor(load_campaign_config(CONFIG), MalformedEngineKit())._run_match(
            pair_identity(load_campaign_config(CONFIG), 1), "A", match_root
        )
    assert any("cleanup also failed: RuntimeError: cleanup failed" in note for note in caught.value.__notes__)
    failure = json.loads((match_root / "analysis_failure.json").read_text(encoding="utf-8"))
    assert failure["physical_seat"] == "O"
    assert failure["engine"] == "sage"
    assert failure["decision_type"] == "checker"
    assert failure["exception_type"] == "MatchExecutionError"
    assert failure["returned_result"]["checker_decision"] == {"recommended_move_id": "missing"}
    assert failure["returned_raw_evidence"][0]["value"]["inline"] == "raw engine response"
    result_record = json.loads((match_root / "analysis_results.jsonl").read_text(encoding="utf-8"))
    assert result_record["returned_result"] == failure["returned_result"]


def test_analysis_journal_paths_are_directory_durable_before_board_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    fsync_kinds: list[str] = []
    real_fsync = match_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        fsync_kinds.append("directory" if stat.S_ISDIR(match_module.os.fstat(descriptor).st_mode) else "file")
        real_fsync(descriptor)

    match_root = tmp_path / "match-A"

    class StopBeforeBoardStart:
        def __init__(self, *_: object) -> None:
            assert (match_root / "analysis_requests.jsonl").read_bytes() == b""
            assert (match_root / "analysis_results.jsonl").read_bytes() == b""
            assert fsync_kinds == ["file", "directory", "file", "directory"]
            raise RuntimeError("stop before board start")

    monkeypatch.setattr(match_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", StopBeforeBoardStart)
    config = load_campaign_config(CONFIG)
    with pytest.raises(RuntimeError, match="stop before board start"):
        PairExecutor(config, FakeEngineKit())._run_match(pair_identity(config, 1), "A", match_root)


def test_gnu_command_errors_fail_closed() -> None:
    with pytest.raises(MatchExecutionError, match="GNU rejected"):
        _raise_on_gnu_error("set rng manual", "Error: unsupported RNG\n")
    _raise_on_gnu_error("set rng manual", "GNU Backgammon ready\n")


def test_opening_transition_binds_ties_winner_and_board_state() -> None:
    consumed = [
        {"prompt_type": "opening", "game_number": 2, "roll_index": 1, "physical_seat": "O", "engine": "sage", "die1": 2, "die2": None},
        {"prompt_type": "opening", "game_number": 2, "roll_index": 1, "physical_seat": "X", "engine": "gnu", "die1": 2, "die2": None},
        {"prompt_type": "opening", "game_number": 2, "roll_index": 2, "physical_seat": "O", "engine": "sage", "die1": 1, "die2": None},
        {"prompt_type": "opening", "game_number": 2, "roll_index": 2, "physical_seat": "X", "engine": "gnu", "die1": 4, "die2": None},
    ]
    observed = position(1, "player_1", "none", (4, 1))
    assert _validate_opening_transition(consumed, 2, observed, {"O": "sage", "X": "gnu"}, "O") == "X"

    wrong_game = [{**entry, "game_number": 3} if index == 0 else entry for index, entry in enumerate(consumed)]
    with pytest.raises(MatchExecutionError, match="wrong game/seat"):
        _validate_opening_transition(wrong_game, 2, observed, {"O": "sage", "X": "gnu"}, "O")
    tied_final = [*consumed[:-1], {**consumed[-1], "die1": 1}]
    with pytest.raises(MatchExecutionError, match="final non-tied"):
        _validate_opening_transition(tied_final, 2, observed, {"O": "sage", "X": "gnu"}, None)
    with pytest.raises(MatchExecutionError, match="physical seat on roll"):
        _validate_opening_transition(
            consumed, 2, position(1, "player_0", "none", (4, 1)), {"O": "sage", "X": "gnu"}, "O"
        )
    with pytest.raises(MatchExecutionError, match="board dice"):
        _validate_opening_transition(consumed, 2, position(1, "player_1", "none", (6, 1)), {"O": "sage", "X": "gnu"}, "O")
    with pytest.raises(MatchExecutionError, match="missing or incomplete"):
        _validate_opening_transition([], 2, observed, {"O": "sage", "X": "gnu"}, "O")
    non_tied_before_final = [{**consumed[1], "die1": 3}, *consumed[2:]]
    with pytest.raises(MatchExecutionError, match="non-final non-tied"):
        _validate_opening_transition(
            [consumed[0], *non_tied_before_final], 2, observed,
            {"O": "sage", "X": "gnu"}, "O",
        )
    with pytest.raises(MatchExecutionError, match="physical-seat stream"):
        _validate_opening_transition(consumed, 2, observed, {"O": "sage", "X": "gnu"}, "X")
    with pytest.raises(MatchExecutionError, match="state is missing or malformed"):
        _validate_opening_transition(
            consumed, 2, position(1, "player_1", "none", (4.0, 1)),
            {"O": "sage", "X": "gnu"}, "O",
        )


@pytest.mark.parametrize(
    "sgf,text,error",
    [
        ("", "7 point match\nGame 1\nplayers\n", "SGF"),
        ("(;FF[4]GM[6]", "7 point match\nGame 1\nplayers\n", "SGF"),
        ("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7])junk)", "7 point match\nGame 1\nplayers\n", "SGF"),
        ("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7])\n", "", "match text"),
        ("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7])\n", "7 point match\nGame 1\nplayers\n", "match text"),
    ],
)
def test_fake_board_native_outputs_reject_empty_or_truncated_files(
    tmp_path: Path, sgf: str, text: str, error: str
) -> None:
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text(sgf, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    with pytest.raises(MatchExecutionError, match=error):
        _validate_native_outputs(sgf_path, text_path, {"O": "sage", "X": "gnu"})


@pytest.mark.parametrize(
    "engine_by_seat,text",
    [
        ({"O": "sage", "X": "gnu"}, "7 point match\n\n Game 1\n sage_seat_O : 0  gnu_seat_X : 0\n"),
        ({"O": "gnu", "X": "sage"}, "7 point match\n\n Game 1\n sage_seat_X : 0  gnu_seat_O : 0\n"),
    ],
)
def test_fake_board_native_outputs_accept_exact_side_player_mappings(
    tmp_path: Path, engine_by_seat: dict[str, str], text: str
) -> None:
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7][game:0])\n", encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    _validate_native_outputs(sgf_path, text_path, engine_by_seat)


@pytest.mark.parametrize(
    "players",
    [
        "sage_seat_O : 0",
        "sage_seat_O : 0  sage_seat_O : 0  gnu_seat_X : 0",
        "sage_seat_O : 0  gnu_seat_X : 0  sage_seat_X : 0",
        "sage_seat_O : 0  gnu_seat_O : 0",
        "sage_seat_X : 0  gnu_seat_O : 0",
    ],
    ids=["missing", "duplicate", "extra", "cross-wired", "wrong-side"],
)
def test_fake_board_native_outputs_reject_nonexact_side_player_mappings(
    tmp_path: Path, players: str
) -> None:
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7][game:0])\n", encoding="utf-8")
    text_path.write_text(f"7 point match\n\n Game 1\n {players}\n", encoding="utf-8")
    with pytest.raises(MatchExecutionError, match="match text"):
        _validate_native_outputs(sgf_path, text_path, {"O": "sage", "X": "gnu"})
