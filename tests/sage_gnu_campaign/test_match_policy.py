from __future__ import annotations

import hashlib
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
    GnuBoardProcess,
    MatchExecutionError,
    PairExecutor,
    _board_environment,
    _parse_terminal_event,
    _raise_on_gnu_error,
    _validate_command_transition,
    _validate_native_outputs,
    _validate_opening_transition,
    _recommended_checker_notation,
    pending_double_response,
    pre_roll_cube_action,
)
from tests.sage_gnu_campaign.native_fixtures import native_documents


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


def test_board_constructor_cleanup_failure_preserves_primary_startup_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    class KnownStartupError(RuntimeError):
        pass

    class KnownCleanupError(RuntimeError):
        pass

    startup_error = KnownStartupError("known startup failure")
    cleanup_error = KnownCleanupError("known cleanup failure")
    original_cleanup = GnuBoardProcess._cleanup
    isolated_home = tmp_path / "gnubg-home"

    def fail_startup() -> tuple[int, int]:
        raise startup_error

    def clean_then_fail(self: GnuBoardProcess, *, terminate: bool) -> None:
        original_cleanup(self, terminate=terminate)
        raise cleanup_error

    monkeypatch.setattr(match_module.pty, "openpty", fail_startup)
    monkeypatch.setattr(GnuBoardProcess, "_cleanup", clean_then_fail)
    constructed = None
    with pytest.raises(KnownStartupError) as caught:
        constructed = GnuBoardProcess(
            Path("/fake/gnubg"), {}, FakeDice(tmp_path / "dice"), isolated_home
        )

    assert caught.value is startup_error
    assert constructed is None
    assert not isolated_home.exists()
    assert any(
        "GNU board constructor cleanup also failed: KnownCleanupError: known cleanup failure" in note
        for note in caught.value.__notes__
    )


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
    def __init__(self, root: Path, **values: object):
        self.root = root
        self.engine_by_seat = values.get("engine_by_seat", {"O": "sage", "X": "gnu"})
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
        consumption = self.root / "seat_dice_consumption.jsonl"
        consumption.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in self.consumption),
            encoding="utf-8",
        )
        write_json(manifest, {
            "engine_by_physical_seat": self.engine_by_seat,
            "streams": [
                {
                    "game_number": game,
                    "physical_seat": seat,
                    "engine": self.engine_by_seat[seat],
                }
                for game in (1, 2) for seat in ("O", "X")
            ],
            "consumption": {
                "path": consumption.name,
                "entries": len(self.consumption),
                "sha256": hashlib.sha256(consumption.read_bytes()).hexdigest(),
            },
        })
        return manifest, consumption


class FakeBoard:
    last: "FakeBoard | None" = None

    def __init__(self, *_: object):
        self.dice = _[2]
        self.commands: list[str] = []
        self.transcript: list[dict[str, str]] = []
        self.board_index = 0
        self.move_count = 0
        self.roll_count = 0
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
            self.roll_count += 1
            seat = self.dice.expected_next_roll_seat or "X"
            self.dice.consumption.append(
                {"prompt_type": "checker", "game_number": self.dice.current_game_number, "physical_seat": seat, "engine": "gnu" if seat == "X" else "sage", "die1": 3, "die2": 1}
            )
            self.dice.expected_next_roll_seat = "X" if seat == "O" else "O"
        if command in {"1/off", "13/8"}:
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
                return (
                    "sage_seat_O moves 1/off.\n"
                    "sage_seat_O wins a backgammon and 6 points.\n"
                )
        if command == "take":
            self.dice.consumption.append(
                {"prompt_type": "checker", "game_number": 2, "physical_seat": "X", "engine": "gnu", "die1": 2, "die2": 2}
            )
            self.dice.expected_next_roll_seat = "O"
        if command == "show board":
            self.board_index += 1
            return f"Position ID: P{self.board_index}\nMatch ID: M{self.board_index}\n"
        if command.startswith("save match "):
            path = Path(command.removeprefix("save match "))
            sgf, _, _ = native_documents({"O": "sage", "X": "gnu"}, [("O", 6), ("O", 2)])
            path.write_text(sgf, encoding="utf-8")
        if command.startswith("export match text "):
            path = Path(command.removeprefix("export match text "))
            _, text, _ = native_documents({"O": "sage", "X": "gnu"}, [("O", 6), ("O", 2)])
            path.write_text(text, encoding="utf-8")
        if command == "pass":
            return (
                "gnu_seat_X refuses the cube and gives up 2 points.\n"
                "sage_seat_O wins a single game and 2 points.\n"
            )
        return "ok"

    def close(self) -> None:
        pass


def fake_board_state(
    player_0: dict[int, int] | None = None,
    player_1: dict[int, int] | None = None,
    *,
    bar_0: int = 0,
    bar_1: int = 0,
    off_0: int = 0,
    off_1: int = 0,
) -> object:
    def points(values: dict[int, int] | None) -> tuple[int, ...]:
        result = [0] * 24
        for point_number, count in (values or {}).items():
            result[point_number - 1] = count
        return tuple(result)

    return SimpleNamespace(
        checker_count=SimpleNamespace(player_0=15, player_1=15),
        player_0=SimpleNamespace(points=points(player_0), bar=bar_0, off=off_0),
        player_1=SimpleNamespace(points=points(player_1), bar=bar_1, off=off_1),
    )


START = fake_board_state(
    {24: 2, 13: 5, 8: 3, 6: 5},
    {24: 2, 13: 5, 8: 3, 6: 5},
)


def position(
    score: int,
    player: str,
    pending: str,
    dice: tuple[int, int] | None,
    *,
    board: object = START,
    player_1_score: int = 0,
    on_roll: str | None = None,
    offerer: str | None = None,
    responder: str | None = None,
    offered_cube_value: int | None = None,
    resignation_multiplier: int | None = None,
    cube_value: int = 1,
    cube_owner: str = "center",
    game_state: str = "playing",
) -> object:
    return SimpleNamespace(
        board=board,
        score=SimpleNamespace(player_0=score, player_1=player_1_score, match_length=7),
        state=SimpleNamespace(
            game_state=game_state,
            decision_player=player,
            on_roll=on_roll if on_roll is not None else player,
            dice=dice,
        ),
        cube=SimpleNamespace(
            value=cube_value,
            owner=cube_owner,
            pending_action=SimpleNamespace(
                type=pending,
                offerer=offerer,
                responder=responder,
                offered_cube_value=offered_cube_value,
                resignation_multiplier=resignation_multiplier,
            ),
        ),
    )


def terminal_event(
    kind: str, winner_seat: str, points: int, *, loser_seat: str | None = None,
    resignation_level: int | None = None, result_level: int = 1,
) -> dict[str, object]:
    engine_by_seat = {"O": "sage", "X": "gnu"}
    event: dict[str, object] = {
        "kind": kind,
        "winner_physical_seat": winner_seat,
        "winner_engine": engine_by_seat[winner_seat],
        "points": points,
        "result_level": result_level,
    }
    if loser_seat is not None:
        event.update({
            "loser_physical_seat": loser_seat,
            "loser_engine": engine_by_seat[loser_seat],
        })
    if resignation_level is not None:
        event["resignation_level"] = resignation_level
    return event


class FakeEngineKit:
    def __init__(self) -> None:
        self.gnu_runtime = SimpleNamespace(executable=Path("/fake/gnubg"), environment=lambda: {})
        first_game = fake_board_state({1: 1}, {24: 14}, bar_1=1, off_0=14)
        x_moved = fake_board_state(
            {24: 2, 13: 5, 8: 3, 6: 5},
            {24: 2, 13: 4, 8: 4, 6: 5},
        )
        both_moved = fake_board_state(
            {24: 2, 13: 4, 9: 1, 8: 3, 6: 5},
            {24: 2, 13: 4, 8: 4, 6: 5},
        )
        x_moved_again = fake_board_state(
            {24: 2, 13: 4, 9: 1, 8: 3, 6: 5},
            {24: 2, 13: 3, 9: 1, 8: 4, 6: 5},
        )
        self.position_values = [
                position(0, "player_0", "none", (3, 1), board=first_game, cube_value=2),
                position(6, "player_1", "none", (4, 1)),
                position(6, "player_0", "none", None, board=x_moved),
                position(6, "player_0", "none", (3, 1), board=x_moved),
                position(6, "player_1", "none", None, board=both_moved),
                position(
                    6, "player_0", "double", None, board=both_moved,
                    on_roll="player_1", offerer="player_1", responder="player_0",
                    offered_cube_value=2,
                ),
                position(
                    6, "player_1", "none", (2, 2), board=both_moved,
                    cube_value=2, cube_owner="player_0",
                ),
                position(
                    6, "player_0", "none", None, board=x_moved_again,
                    cube_value=2, cube_owner="player_0",
                ),
                position(
                    6, "player_1", "double", None, board=x_moved_again,
                    on_roll="player_0", offerer="player_0", responder="player_1",
                    offered_cube_value=4, cube_value=2, cube_owner="player_0",
                ),
                position(
                    8, None, "none", None, board=x_moved_again,
                    on_roll="player_0", cube_value=2, cube_owner="player_0",
                    game_state="game_over",
                ),
        ]
        self.positions = iter(self.position_values)
        self.analysis_calls: list[tuple[str, str]] = []
        self.checker_commands = iter(["1/off", "13/9/8", "13/10/9", "13/11/9"])

    def position_from_gnuid(self, gnuid: str) -> object:
        assert gnuid.startswith("P")
        if not hasattr(self, "_position_cache"):
            self._position_cache = list(self.positions)
        index = int(gnuid.split(":", 1)[0].removeprefix("P")) - 1
        if index == 0 and not hasattr(self, "_initial_opening_returned"):
            self._initial_opening_returned = True
            return position(0, "player_0", "none", (3, 1))
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
            notation = next(self.checker_commands)
            return {
                "checker_decision": {
                    "recommended_move_id": "m1",
                    "candidates": [{"move_id": "m1", "notation": notation}],
                }
            }
        cube_call = len([call for call in self.analysis_calls if call[1] == "cube"])
        if cube_call == 1:
            return {"cube_decision": cube_decision(0.2, 0.8, "no-double")}
        if cube_call == 2:
            return {"cube_decision": cube_decision(0.2, 0.8, "double-take")}
        if cube_call == 3:
            return {"cube_decision": cube_decision(0.2, 0.8)}
        if cube_call == 4:
            return {"cube_decision": cube_decision(0.2, 0.8, "double-take")}
        return {"cube_decision": cube_decision(0.8, 0.2)}

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
        ("sage", "cube"),
        ("gnu", "checker"),
        ("sage", "cube"),
        ("gnu", "cube"),
    ]
    assert FakeBoard.last is not None
    assert [command for command in FakeBoard.last.commands if command in {
        "roll", "double", "take", "pass", "1/off", "13/9/8", "13/10/9", "13/11/9"
    }] == [
        "1/off",
        "13/9/8",
        "roll",
        "13/10/9",
        "double",
        "take",
        "13/11/9",
        "double",
        "pass",
    ]
    assert not any(
        command == "hint" or command.startswith("hint ") or command == "show evaluation"
        for command in FakeBoard.last.commands
    )
    decisions = [
        json.loads(line)
        for line in (tmp_path / "match-A/decisions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    first_terminal = decisions[0]["transition_evidence"]
    assert first_terminal["command_type"] == "checker"
    assert first_terminal["acting_physical_seat"] == "O"
    assert first_terminal["acting_engine"] == "sage"
    assert first_terminal["pre_command"]["gnuid"] == decisions[0]["gnuid"]
    assert first_terminal["terminal_event"] == terminal_event(
        "ordinary_game_over", "O", 6, result_level=3
    )
    assert first_terminal["post_command"]["score"] == [6, 0]
    assert first_terminal["game_number"] == 1
    assert first_terminal["subsequent_opening_state"]["game_number"] == 2
    final_terminal = decisions[-1]["transition_evidence"]
    assert final_terminal["command_type"] == "pass"
    assert final_terminal["terminal_event"] == terminal_event(
        "drop", "O", 2, loser_seat="X"
    )
    assert final_terminal["post_command"]["score"] == [8, 0]
    assert final_terminal["subsequent_opening_state"] is None


@pytest.mark.parametrize(
    "case,index",
    [
        ("checker-pending-double", 2),
        ("checker-board-unchanged", 2),
        ("incorrect-next-player-on-roll", 2),
        ("wrong-cube-after-double", 5),
        ("take-followed-by-resignation", 6),
        ("wrong-cube-after-take", 6),
        ("wrong-resulting-board-dice", 6),
        ("wrong-cube-after-pass", 9),
    ],
)
def test_changed_gnuid_with_wrong_command_specific_state_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    index: int,
) -> None:
    """Every fake show-board response changes ID; semantic corruption must still fail."""
    import runner.sage_gnu_campaign.match as match_module

    engine_kit = FakeEngineKit()
    changed = engine_kit.position_values[index]
    if case == "checker-pending-double":
        changed.cube.pending_action = SimpleNamespace(
            type="double", offerer="player_1", responder="player_0",
            offered_cube_value=2, resignation_multiplier=None,
        )
    elif case == "checker-board-unchanged":
        changed.board = START
    elif case == "incorrect-next-player-on-roll":
        changed.state.on_roll = "player_1"
    elif case == "wrong-cube-after-double":
        changed.cube.value = 2
    elif case == "take-followed-by-resignation":
        changed.cube.pending_action = SimpleNamespace(
            type="resignation", offerer="player_0", responder="player_1",
            offered_cube_value=None, resignation_multiplier=1,
        )
    elif case == "wrong-cube-after-take":
        changed.cube.owner = "player_1"
    elif case == "wrong-resulting-board-dice":
        changed.state.dice = (2, 3)
    elif case == "wrong-cube-after-pass":
        changed.cube.value = 4

    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", FakeBoard)
    with pytest.raises(MatchExecutionError):
        PairExecutor(load_campaign_config(CONFIG), engine_kit)._run_match(
            pair_identity(load_campaign_config(CONFIG), 1), "A", tmp_path / case
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("physical_seat", "O"),
        ("engine", "sage"),
    ],
    ids=["correct-dice-wrong-physical-seat", "correct-dice-wrong-engine"],
)
def test_changed_gnuid_with_next_dice_bound_to_wrong_seat_or_engine_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    class WrongTakeDiceBoard(FakeBoard):
        def send(self, command: str, timeout_seconds: float = 60.0) -> str:
            output = super().send(command, timeout_seconds)
            if command == "take":
                self.dice.consumption[-1][field] = value
            return output

    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", WrongTakeDiceBoard)
    with pytest.raises(MatchExecutionError, match="wrong game, physical seat, or engine"):
        PairExecutor(load_campaign_config(CONFIG), FakeEngineKit())._run_match(
            pair_identity(load_campaign_config(CONFIG), 1), "A", tmp_path / field
        )


def test_resignation_accept_requires_exact_score_cube_action_and_turn_state() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        4, "player_0", "resignation", None,
        on_roll="player_1", offerer="player_1", responder="player_0",
        resignation_multiplier=2, cube_value=2,
    )
    after = position(
        8, None, "none", None,
        on_roll="player_1", cube_value=2, game_state="resigned",
    )
    _validate_command_transition(
        "accept", before, after, [], 1, "O", "sage", mapping, "X",
        terminal_event("resignation", "O", 4, resignation_level=2, result_level=2),
    )

    after.cube.pending_action = SimpleNamespace(
        type="resignation", offerer="player_1", responder="player_0",
        offered_cube_value=None, resignation_multiplier=2,
    )
    with pytest.raises(MatchExecutionError, match="cube or turn ownership"):
        _validate_command_transition(
            "accept", before, after, [], 1, "O", "sage", mapping, "X",
            terminal_event("resignation", "O", 4, resignation_level=2, result_level=2),
        )

    mislabeled = position(
        8, None, "none", None,
        on_roll="player_1", cube_value=2, game_state="game_over",
    )
    with pytest.raises(MatchExecutionError, match="invalid turn/action state"):
        _validate_command_transition(
            "accept", before, mislabeled, [], 1, "O", "sage", mapping, "X",
            terminal_event("resignation", "O", 4, resignation_level=2, result_level=2),
        )


def test_checker_move_reconciles_an_automatically_consumed_next_roll_exactly() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_0", "none", (5, 1),
        board=fake_board_state({13: 1}, {24: 1}, off_0=14, off_1=14),
    )
    after = position(
        0, "player_1", "none", (4, 2),
        board=fake_board_state({8: 1}, {24: 1}, off_0=14, off_1=14),
    )
    consumed = [{
        "prompt_type": "checker", "game_number": 1, "physical_seat": "X",
        "engine": "gnu", "die1": 4, "die2": 2,
    }]
    _validate_command_transition(
        "13/8", before, after, consumed, 1, "O", "sage", mapping, "O", None
    )

    after.state.dice = (2, 4)
    with pytest.raises(MatchExecutionError, match="wrong board, cube, action, dice, or turn"):
        _validate_command_transition(
            "13/8", before, after, consumed, 1, "O", "sage", mapping, "O", None
        )


@pytest.mark.parametrize(
    "case",
    [
        "stale-offerer",
        "stale-responder",
        "stale-offered-cube",
        "stale-resignation",
        "pending-double",
    ],
)
def test_checker_transition_rejects_every_stale_action_field(case: str) -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_0", "none", (5, 1),
        board=fake_board_state({13: 1}, {24: 1}, off_0=14, off_1=14),
    )
    after = position(
        0, "player_1", "none", None,
        board=fake_board_state({8: 1}, {24: 1}, off_0=14, off_1=14),
    )
    pending = after.cube.pending_action
    if case == "stale-offerer":
        pending.offerer = "player_0"
    elif case == "stale-responder":
        pending.responder = "player_1"
    elif case == "stale-offered-cube":
        pending.offered_cube_value = 2
    elif case == "stale-resignation":
        pending.resignation_multiplier = 1
    elif case == "pending-double":
        pending.type = "double"
        pending.offerer = "player_0"
        pending.responder = "player_1"
        pending.offered_cube_value = 2

    with pytest.raises(MatchExecutionError, match="wrong board, cube, action, dice, or turn"):
        _validate_command_transition(
            "13/8", before, after, [], 1, "O", "sage", mapping, "X", None
        )


def test_checker_transition_requires_command_to_agree_with_previous_dice() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_0", "none", (4, 1),
        board=fake_board_state({13: 1}, {24: 1}, off_0=14, off_1=14),
    )
    after = position(
        0, "player_1", "none", None,
        board=fake_board_state({8: 1}, {24: 1}, off_0=14, off_1=14),
    )
    with pytest.raises(MatchExecutionError, match="prior dice"):
        _validate_command_transition(
            "13/8", before, after, [], 1, "O", "sage", mapping, "X", None
        )


def test_roll_transition_rejects_unexpected_action_metadata() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(0, "player_0", "none", None)
    after = position(0, "player_0", "none", (3, 2))
    consumed = [{
        "prompt_type": "checker", "game_number": 1, "physical_seat": "O",
        "engine": "sage", "die1": 3, "die2": 2,
    }]
    _validate_command_transition(
        "roll", before, after, consumed, 1, "O", "sage", mapping, "X", None
    )
    after.cube.pending_action.resignation_multiplier = 1
    with pytest.raises(MatchExecutionError, match="changed checker or cube state"):
        _validate_command_transition(
            "roll", before, after, consumed, 1, "O", "sage", mapping, "X", None
        )


@pytest.mark.parametrize("case", ["wrong-owner", "wrong-value", "resignation", "stale-offer"])
def test_take_transition_requires_exact_cube_and_cleared_action(case: str) -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_1", "double", None,
        on_roll="player_0", offerer="player_0", responder="player_1",
        offered_cube_value=2,
    )
    after = position(
        0, "player_0", "none", None,
        cube_value=2, cube_owner="player_1",
    )
    if case == "wrong-owner":
        after.cube.owner = "player_0"
    elif case == "wrong-value":
        after.cube.value = 4
    elif case == "resignation":
        after.cube.pending_action.type = "resignation"
        after.cube.pending_action.offerer = "player_0"
        after.cube.pending_action.responder = "player_1"
        after.cube.pending_action.resignation_multiplier = 1
    elif case == "stale-offer":
        after.cube.pending_action.offered_cube_value = 2

    with pytest.raises(MatchExecutionError, match="wrong cube, action, dice, or turn state"):
        _validate_command_transition(
            "take", before, after, [], 1, "X", "gnu", mapping, "O", None
        )


def test_pass_requires_game_over_not_resigned() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        6, "player_1", "double", None,
        on_roll="player_0", offerer="player_0", responder="player_1",
        offered_cube_value=2,
    )
    valid = position(7, None, "none", None, on_roll="player_0", game_state="game_over")
    _validate_command_transition(
        "pass", before, valid, [], 1, "X", "gnu", mapping, "O",
        terminal_event("drop", "O", 1, loser_seat="X"),
    )
    mislabeled = position(7, None, "none", None, on_roll="player_0", game_state="resigned")
    with pytest.raises(MatchExecutionError, match="invalid turn/action state"):
        _validate_command_transition(
            "pass", before, mislabeled, [], 1, "X", "gnu", mapping, "O",
            terminal_event("drop", "O", 1, loser_seat="X"),
        )


def test_normal_bearoff_requires_game_over_not_resigned() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before_board = fake_board_state({1: 1}, {24: 1}, off_0=14, off_1=14)
    final_board = fake_board_state({}, {24: 1}, off_0=15, off_1=14)
    before = position(6, "player_0", "none", (1, 1), board=before_board)
    valid = position(
        7, None, "none", None, board=final_board,
        on_roll="player_0", game_state="game_over",
    )
    _validate_command_transition(
        "1/off", before, valid, [], 1, "O", "sage", mapping, "X",
        terminal_event("ordinary_game_over", "O", 1),
    )
    mislabeled = position(
        7, None, "none", None, board=final_board,
        on_roll="player_0", game_state="resigned",
    )
    with pytest.raises(MatchExecutionError, match="invalid turn/action state"):
        _validate_command_transition(
            "1/off", before, mislabeled, [], 1, "O", "sage", mapping, "X",
            terminal_event("ordinary_game_over", "O", 1),
        )


def next_game_opening(score_o: int, score_x: int = 0) -> tuple[object, list[dict[str, object]]]:
    opening = position(
        score_o, "player_0", "none", (4, 2), player_1_score=score_x,
        on_roll="player_0",
    )
    consumed = [
        {
            "prompt_type": "opening", "game_number": 2, "roll_index": 1,
            "physical_seat": "O", "engine": "sage", "die1": 4, "die2": None,
        },
        {
            "prompt_type": "opening", "game_number": 2, "roll_index": 1,
            "physical_seat": "X", "engine": "gnu", "die1": 2, "die2": None,
        },
    ]
    return opening, consumed


def test_nonfinal_pass_rejects_resignation_with_right_score_and_opening() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_1", "double", None, on_roll="player_0",
        offerer="player_0", responder="player_1", offered_cube_value=2,
    )
    after, consumed = next_game_opening(1)
    with pytest.raises(MatchExecutionError, match="exact drop semantics"):
        _validate_command_transition(
            "pass", before, after, consumed, 1, "X", "gnu", mapping, "X",
            terminal_event("resignation", "O", 1, resignation_level=1),
        )


def test_nonfinal_resignation_rejects_game_over_with_right_score_and_opening() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_0", "resignation", None, on_roll="player_1",
        offerer="player_1", responder="player_0", resignation_multiplier=2,
    )
    after, consumed = next_game_opening(2)
    with pytest.raises(MatchExecutionError, match="exact resignation semantics"):
        _validate_command_transition(
            "accept", before, after, consumed, 1, "O", "sage", mapping, "X",
            terminal_event("ordinary_game_over", "O", 2, result_level=2),
        )


@pytest.mark.parametrize("wrong_kind", ["resignation", "drop"])
def test_nonfinal_bearoff_rejects_substituted_terminal_kind_with_right_transition(
    wrong_kind: str,
) -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_0", "none", (1, 1),
        board=fake_board_state({1: 1}, {24: 1}, off_0=14, off_1=14),
    )
    after, consumed = next_game_opening(1)
    event = terminal_event(
        wrong_kind, "O", 1,
        loser_seat="X" if wrong_kind == "drop" else None,
        resignation_level=1 if wrong_kind == "resignation" else None,
    )
    with pytest.raises(MatchExecutionError, match="ordinary game-over semantics"):
        _validate_command_transition(
            "1/off", before, after, consumed, 1, "O", "sage", mapping, "X", event,
        )


def test_nonfinal_terminal_event_rejects_right_kind_with_wrong_score() -> None:
    mapping = {"O": "sage", "X": "gnu"}
    before = position(
        0, "player_1", "double", None, on_roll="player_0",
        offerer="player_0", responder="player_1", offered_cube_value=2,
    )
    after, consumed = next_game_opening(2)
    with pytest.raises(MatchExecutionError, match="wrong score or winner"):
        _validate_command_transition(
            "pass", before, after, consumed, 1, "X", "gnu", mapping, "X",
            terminal_event("drop", "O", 1, loser_seat="X"),
        )


def test_terminal_output_parser_preserves_exact_native_semantics() -> None:
    drop = _parse_terminal_event(
        "gnu_seat_X refuses the cube and gives up 2 points.\n"
        "sage_seat_O wins a single game and 2 points.\n"
    )
    resignation = _parse_terminal_event(
        "sage_seat_O accepts and wins a gammon.\n"
        "sage_seat_O wins a gammon and 4 points.\n"
    )
    ordinary = _parse_terminal_event("sage_seat_O wins a backgammon and 6 points.\n")
    assert drop == terminal_event("drop", "O", 2, loser_seat="X")
    assert resignation == terminal_event(
        "resignation", "O", 4, resignation_level=2, result_level=2
    )
    assert ordinary == terminal_event("ordinary_game_over", "O", 6, result_level=3)


def test_terminal_output_parser_rejects_ambiguous_or_conflicting_events() -> None:
    with pytest.raises(MatchExecutionError, match="action semantics"):
        _parse_terminal_event("gnu_seat_X refuses the cube and gives up 1 point.\n")
    with pytest.raises(MatchExecutionError, match="ambiguous"):
        _parse_terminal_event(
            "sage_seat_O accepts and wins a single game.\n"
            "gnu_seat_X refuses the cube and gives up 1 point.\n"
            "sage_seat_O wins a single game and 1 point.\n"
        )


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
    import runner.sage_gnu_campaign.environment as environment_module

    fsync_events: list[tuple[str, Path]] = []
    real_fsync = match_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(match_module.os.fstat(descriptor).st_mode) else "file"
        descriptor_path = Path(match_module.os.readlink(f"/proc/self/fd/{descriptor}"))
        fsync_events.append((kind, descriptor_path))
        real_fsync(descriptor)

    monkeypatch.setattr(match_module.os, "fsync", recording_fsync)
    config = load_campaign_config(CONFIG)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runner_root = environment_module.durably_establish_runner_workspace(config, runtime_root)
    pair_directory = runner_root / "pair-000001"
    workspace = pair_directory / "attempt-1"
    workspace.mkdir(parents=True)
    match_root = workspace / "pair-output"
    matches = match_root / "matches"
    side_a = matches / "A"
    side_b = matches / "B"

    class StopBeforeBoardStart:
        def __init__(self, *_: object) -> None:
            assert (side_a / "decisions.jsonl").read_bytes() == b""
            assert (side_a / "analysis_requests.jsonl").read_bytes() == b""
            assert (side_a / "analysis_results.jsonl").read_bytes() == b""
            assert side_b.is_dir()
            assert fsync_events == [
                ("directory", runtime_root),
                ("directory", runner_root.parent),
                ("directory", runtime_root),
                ("directory", runner_root),
                ("directory", runner_root.parent),
                ("directory", workspace),
                ("directory", pair_directory),
                ("directory", runner_root),
                ("directory", match_root),
                ("directory", workspace),
                ("directory", matches),
                ("directory", match_root),
                ("directory", side_a),
                ("directory", matches),
                ("directory", side_b),
                ("directory", matches),
                ("file", side_a / "decisions.jsonl"),
                ("directory", side_a),
                ("file", side_a / "analysis_requests.jsonl"),
                ("directory", side_a),
                ("file", side_a / "analysis_results.jsonl"),
                ("directory", side_a),
            ]
            raise RuntimeError("stop before board start")

    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", StopBeforeBoardStart)
    with pytest.raises(RuntimeError, match="stop before board start"):
        PairExecutor(config, FakeEngineKit()).run(pair_identity(config, 1), workspace)


def test_decision_journal_parent_fsync_failure_prevents_board_or_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    match_root = tmp_path / "match-A"
    board_started = False

    class BoardMustNotStart:
        def __init__(self, *_: object) -> None:
            nonlocal board_started
            board_started = True
            raise AssertionError("board started before decisions.jsonl was directory-durable")

    real_fsync_directory = match_module.fsync_directory

    def fail_decision_parent(path: Path) -> None:
        resolved = Path(path)
        if (
            resolved == match_root
            and (match_root / "decisions.jsonl").exists()
            and not (match_root / "analysis_requests.jsonl").exists()
        ):
            raise OSError("simulated decisions parent fsync crash window")
        real_fsync_directory(path)

    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", BoardMustNotStart)
    monkeypatch.setattr(match_module, "fsync_directory", fail_decision_parent)
    with pytest.raises(OSError, match="decisions parent fsync"):
        PairExecutor(load_campaign_config(CONFIG), FakeEngineKit())._run_match(
            pair_identity(load_campaign_config(CONFIG), 1), "A", match_root
        )
    assert not board_started
    assert (match_root / "decisions.jsonl").is_file()
    assert not (match_root / "analysis_requests.jsonl").exists()


def test_decision_journal_creation_and_append_have_exact_fsync_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runner.sage_gnu_campaign.match as match_module

    path = tmp_path / "decisions.jsonl"
    events: list[tuple[str, Path]] = []
    real_fsync = match_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(match_module.os.fstat(descriptor).st_mode) else "file"
        events.append((kind, Path(match_module.os.readlink(f"/proc/self/fd/{descriptor}"))))
        real_fsync(descriptor)

    monkeypatch.setattr(match_module.os, "fsync", recording_fsync)
    match_module._create_empty_file_durable(path)
    match_module._append_jsonl_durable(path, {"record_ordinal": 1})
    assert events == [
        ("file", path),
        ("directory", tmp_path),
        ("file", path),
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["record_ordinal"] == 1
    with pytest.raises(FileExistsError):
        match_module._create_empty_file_durable(path)
    assert json.loads(path.read_text(encoding="utf-8"))["record_ordinal"] == 1


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
    assert _validate_opening_transition(
        consumed, 2, observed, {"O": "sage", "X": "gnu"}, "O", (1, 0)
    ) == "X"

    wrong_game = [{**entry, "game_number": 3} if index == 0 else entry for index, entry in enumerate(consumed)]
    with pytest.raises(MatchExecutionError, match="wrong game/seat"):
        _validate_opening_transition(
            wrong_game, 2, observed, {"O": "sage", "X": "gnu"}, "O", (1, 0)
        )
    tied_final = [*consumed[:-1], {**consumed[-1], "die1": 1}]
    with pytest.raises(MatchExecutionError, match="final non-tied"):
        _validate_opening_transition(
            tied_final, 2, observed, {"O": "sage", "X": "gnu"}, None, (1, 0)
        )
    with pytest.raises(MatchExecutionError, match="decision/on-roll physical seat"):
        _validate_opening_transition(
            consumed, 2, position(1, "player_0", "none", (4, 1)),
            {"O": "sage", "X": "gnu"}, "O", (1, 0),
        )
    with pytest.raises(MatchExecutionError, match="board dice"):
        _validate_opening_transition(
            consumed, 2, position(1, "player_1", "none", (6, 1)),
            {"O": "sage", "X": "gnu"}, "O", (1, 0),
        )
    with pytest.raises(MatchExecutionError, match="missing or incomplete"):
        _validate_opening_transition(
            [], 2, observed, {"O": "sage", "X": "gnu"}, "O", (1, 0)
        )
    non_tied_before_final = [{**consumed[1], "die1": 3}, *consumed[2:]]
    with pytest.raises(MatchExecutionError, match="non-final non-tied"):
        _validate_opening_transition(
            [consumed[0], *non_tied_before_final], 2, observed,
            {"O": "sage", "X": "gnu"}, "O", (1, 0),
        )
    with pytest.raises(MatchExecutionError, match="physical-seat stream"):
        _validate_opening_transition(
            consumed, 2, observed, {"O": "sage", "X": "gnu"}, "X", (1, 0)
        )
    with pytest.raises(MatchExecutionError, match="state is missing or malformed"):
        _validate_opening_transition(
            consumed, 2, position(1, "player_1", "none", (4.0, 1)),
            {"O": "sage", "X": "gnu"}, "O", (1, 0),
        )


@pytest.mark.parametrize(
    "case",
    [
        "resigned-opening",
        "wrong-decision-player",
        "wrong-score",
        "stale-offerer",
        "stale-responder",
        "stale-offered-cube",
        "stale-resignation",
        "wrong-cube-owner",
        "wrong-cube-value",
        "wrong-starting-board",
        "pending-action",
        "loser-decision-player",
        "invalid-playing-state",
    ],
)
def test_opening_transition_rejects_wrong_but_changed_hidden_state(case: str) -> None:
    consumed = [
        {"prompt_type": "opening", "game_number": 2, "roll_index": 1, "physical_seat": "O", "engine": "sage", "die1": 1, "die2": None},
        {"prompt_type": "opening", "game_number": 2, "roll_index": 1, "physical_seat": "X", "engine": "gnu", "die1": 4, "die2": None},
    ]
    observed = position(1, "player_1", "none", (4, 1))
    if case in {"resigned-opening", "invalid-playing-state"}:
        observed.state.game_state = "resigned" if case == "resigned-opening" else "game_over"
    elif case in {"wrong-decision-player", "loser-decision-player"}:
        observed.state.decision_player = "player_0"
    elif case == "wrong-score":
        observed.score.player_0 = 2
    elif case == "stale-offerer":
        observed.cube.pending_action.offerer = "player_0"
    elif case == "stale-responder":
        observed.cube.pending_action.responder = "player_1"
    elif case == "stale-offered-cube":
        observed.cube.pending_action.offered_cube_value = 2
    elif case == "stale-resignation":
        observed.cube.pending_action.resignation_multiplier = 1
    elif case == "wrong-cube-owner":
        observed.cube.owner = "player_1"
    elif case == "wrong-cube-value":
        observed.cube.value = 2
    elif case == "wrong-starting-board":
        observed.board = fake_board_state({23: 1}, {24: 2})
    elif case == "pending-action":
        observed.cube.pending_action.type = "double"

    with pytest.raises(MatchExecutionError):
        _validate_opening_transition(
            consumed, 2, observed, {"O": "sage", "X": "gnu"}, "O", (1, 0)
        )


def native_pair(
    engine_by_seat: dict[str, str], games: list[tuple[str, int]],
) -> tuple[str, str]:
    sgf, text, _ = native_documents(engine_by_seat, games)
    return sgf, text


@pytest.mark.parametrize(
    "engine_by_seat,games",
    [
        ({"O": "sage", "X": "gnu"}, [("O", 8)]),
        ({"O": "gnu", "X": "sage"}, [("X", 8)]),
        ({"O": "sage", "X": "gnu"}, [("O", 2), ("X", 1), ("O", 6)]),
        ({"O": "gnu", "X": "sage"}, [("X", 2), ("O", 1), ("X", 6)]),
    ],
    ids=["one-game-side-a", "one-game-side-b", "multi-game-side-a", "multi-game-side-b"],
)
def test_native_outputs_accept_complete_real_shaped_game_collections(
    tmp_path: Path, engine_by_seat: dict[str, str], games: list[tuple[str, int]],
) -> None:
    sgf, text = native_pair(engine_by_seat, games)
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text(sgf, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    summary = _validate_native_outputs(sgf_path, text_path, engine_by_seat)
    assert summary["game_count"] == len(games)
    assert summary["final_score"] == summary["games"][-1]["post_score"]


@pytest.mark.parametrize("bad_sgf", ["", "(;FF[4]GM[6]", "junk(;FF[4]GM[6])"])
def test_native_outputs_reject_empty_truncated_or_outside_sgf(
    tmp_path: Path, bad_sgf: str,
) -> None:
    _, text = native_pair({"O": "sage", "X": "gnu"}, [("O", 8)])
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text(bad_sgf, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    with pytest.raises(MatchExecutionError, match="SGF"):
        _validate_native_outputs(sgf_path, text_path, {"O": "sage", "X": "gnu"})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace("gnu_seat_X : 0", "", 1),
        lambda text: text.replace("gnu_seat_X", "gnu_seat_O", 1),
        lambda text: "sage_seat_O : 0\n" + text,
        lambda text: text + "\ngnu_seat_X : 0\n",
        lambda text: text.replace("Game 2", "Game 3", 1),
    ],
    ids=["missing-player", "cross-wired", "preamble-identity", "trailer-identity", "ordering-gap"],
)
def test_native_outputs_reject_invalid_text_game_structure(
    tmp_path: Path, mutation,
) -> None:
    mapping = {"O": "sage", "X": "gnu"}
    sgf, text = native_pair(mapping, [("O", 2), ("X", 1), ("O", 6)])
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text(sgf, encoding="utf-8")
    text_path.write_text(mutation(text), encoding="utf-8")
    with pytest.raises(MatchExecutionError, match="match text"):
        _validate_native_outputs(sgf_path, text_path, mapping)
