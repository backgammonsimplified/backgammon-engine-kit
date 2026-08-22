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
    GnuBoardProcess,
    MatchExecutionError,
    PairExecutor,
    _board_environment,
    _raise_on_gnu_error,
    _validate_command_transition,
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
                return "sage wins 6 points"
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
            path.write_text("(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7][game:0])\n", encoding="utf-8")
        if command.startswith("export match text "):
            path = Path(command.removeprefix("export match text "))
            path.write_text("7 point match\n\n Game 1\n sage_seat_O : 0  gnu_seat_X : 0\n", encoding="utf-8")
        if command == "pass":
            return "sage wins 2 points"
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
        "accept", before, after, [], 1, "O", "sage", mapping, "X", True
    )

    after.cube.pending_action = SimpleNamespace(
        type="resignation", offerer="player_1", responder="player_0",
        offered_cube_value=None, resignation_multiplier=2,
    )
    with pytest.raises(MatchExecutionError, match="cube or turn ownership"):
        _validate_command_transition(
            "accept", before, after, [], 1, "O", "sage", mapping, "X", True
        )

    mislabeled = position(
        8, None, "none", None,
        on_roll="player_1", cube_value=2, game_state="game_over",
    )
    with pytest.raises(MatchExecutionError, match="invalid turn/action state"):
        _validate_command_transition(
            "accept", before, mislabeled, [], 1, "O", "sage", mapping, "X", True
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
        "13/8", before, after, consumed, 1, "O", "sage", mapping, "O", False
    )

    after.state.dice = (2, 4)
    with pytest.raises(MatchExecutionError, match="wrong board, cube, action, dice, or turn"):
        _validate_command_transition(
            "13/8", before, after, consumed, 1, "O", "sage", mapping, "O", False
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
            "13/8", before, after, [], 1, "O", "sage", mapping, "X", False
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
            "13/8", before, after, [], 1, "O", "sage", mapping, "X", False
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
        "roll", before, after, consumed, 1, "O", "sage", mapping, "X", False
    )
    after.cube.pending_action.resignation_multiplier = 1
    with pytest.raises(MatchExecutionError, match="changed checker or cube state"):
        _validate_command_transition(
            "roll", before, after, consumed, 1, "O", "sage", mapping, "X", False
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
            "take", before, after, [], 1, "X", "gnu", mapping, "O", False
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
        "pass", before, valid, [], 1, "X", "gnu", mapping, "O", True
    )
    mislabeled = position(7, None, "none", None, on_roll="player_0", game_state="resigned")
    with pytest.raises(MatchExecutionError, match="invalid turn/action state"):
        _validate_command_transition(
            "pass", before, mislabeled, [], 1, "X", "gnu", mapping, "O", True
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
        "1/off", before, valid, [], 1, "O", "sage", mapping, "X", True
    )
    mislabeled = position(
        7, None, "none", None, board=final_board,
        on_roll="player_0", game_state="resigned",
    )
    with pytest.raises(MatchExecutionError, match="invalid turn/action state"):
        _validate_command_transition(
            "1/off", before, mislabeled, [], 1, "O", "sage", mapping, "X", True
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

    pair_directory = tmp_path / "pair-000001"
    workspace = pair_directory / "attempt-1"
    workspace.mkdir(parents=True)
    match_module.fsync_directory(tmp_path)
    fsync_events: list[tuple[str, Path]] = []
    real_fsync = match_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(match_module.os.fstat(descriptor).st_mode) else "file"
        descriptor_path = Path(match_module.os.readlink(f"/proc/self/fd/{descriptor}"))
        fsync_events.append((kind, descriptor_path))
        real_fsync(descriptor)

    match_root = workspace / "pair-output"
    matches = match_root / "matches"
    side_a = matches / "A"
    side_b = matches / "B"

    class StopBeforeBoardStart:
        def __init__(self, *_: object) -> None:
            assert (side_a / "analysis_requests.jsonl").read_bytes() == b""
            assert (side_a / "analysis_results.jsonl").read_bytes() == b""
            assert side_b.is_dir()
            assert fsync_events == [
                ("directory", workspace),
                ("directory", pair_directory),
                ("directory", tmp_path),
                ("directory", match_root),
                ("directory", workspace),
                ("directory", matches),
                ("directory", match_root),
                ("directory", side_a),
                ("directory", matches),
                ("directory", side_b),
                ("directory", matches),
                ("file", side_a / "analysis_requests.jsonl"),
                ("directory", side_a),
                ("file", side_a / "analysis_results.jsonl"),
                ("directory", side_a),
            ]
            raise RuntimeError("stop before board start")

    monkeypatch.setattr(match_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(match_module, "SeatDiceController", FakeDice)
    monkeypatch.setattr(match_module, "GnuBoardProcess", StopBeforeBoardStart)
    config = load_campaign_config(CONFIG)
    with pytest.raises(RuntimeError, match="stop before board start"):
        PairExecutor(config, FakeEngineKit()).run(pair_identity(config, 1), workspace)


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
        (
            {"O": "sage", "X": "gnu"},
            "7 point match\n\n Game 1\n sage_seat_O : 0  gnu_seat_X : 0\n"
            "moves\n Game 2\n sage_seat_O : 1  gnu_seat_X : 0\n",
        ),
        (
            {"O": "gnu", "X": "sage"},
            "7 point match\n\n Game 1\n sage_seat_X : 0  gnu_seat_O : 0\n"
            "moves\n Game 2\n sage_seat_X : 0  gnu_seat_O : 2\n"
            "moves\n Game 3\n sage_seat_X : 2  gnu_seat_O : 2\n",
        ),
    ],
    ids=["one-game-side-a", "one-game-side-b", "multi-game-side-a", "multi-game-side-b"],
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


@pytest.mark.parametrize(
    "text",
    [
        "7 point match\nGame 1\nsage_seat_O : 0  gnu_seat_X : 0\nGame 2\nsage_seat_O : 1\n",
        "7 point match\nGame 1\nsage_seat_O : 0  gnu_seat_X : 0\nGame 2\nsage_seat_X : 1  gnu_seat_O : 0\n",
        "7 point match\nGame 1\nsage_seat_O : 0  gnu_seat_X : 0  other_seat_O : 0\n",
        "7 point match\nGame 1\nsage_seat_O : 0  sage_seat_O : 0  gnu_seat_X : 0\n",
        "7 point match\nGame 1\nsage_seat_O : 0  gnu_seat_X : 0\nGame 3\nsage_seat_O : 1  gnu_seat_X : 0\n",
    ],
    ids=[
        "later-game-missing-player",
        "later-game-cross-wired",
        "unexpected-third-player",
        "duplicate-player-in-game",
        "malformed-game-sequence",
    ],
)
def test_fake_board_native_outputs_reject_invalid_per_game_player_sets(
    tmp_path: Path, text: str
) -> None:
    sgf_path = tmp_path / "match.sgf"
    text_path = tmp_path / "match.txt"
    sgf_path.write_text(
        "(;FF[4]GM[6]AP[GNU Backgammon:1.06.002]MI[length:7][game:0])\n",
        encoding="utf-8",
    )
    text_path.write_text(text, encoding="utf-8")
    with pytest.raises(MatchExecutionError, match="match text"):
        _validate_native_outputs(sgf_path, text_path, {"O": "sage", "X": "gnu"})
