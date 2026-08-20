from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.identity import pair_identity
from runner.sage_gnu_campaign.manifests import write_json
from runner.sage_gnu_campaign.match import (
    MatchExecutionError,
    PairExecutor,
    _board_environment,
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

    def prepare_files(self) -> None:
        self.root.mkdir(parents=True)

    def prepare_after_turn(self, game_number: int, physical_seat: str) -> None:
        assert game_number == 1
        assert physical_seat in {"O", "X"}

    def write_evidence(self) -> tuple[Path, Path]:
        manifest = self.root / "seat_dice_manifest.json"
        consumption = self.root / "seat_dice_consumption.json"
        write_json(manifest, {"status": "fake"})
        write_json(consumption, {"status": "fake"})
        return manifest, consumption


class FakeBoard:
    last: "FakeBoard | None" = None

    def __init__(self, *_: object):
        self.commands: list[str] = []
        self.transcript: list[dict[str, str]] = []
        self.board_index = 0
        FakeBoard.last = self

    def send(self, command: str, timeout_seconds: float = 60.0) -> str:
        del timeout_seconds
        self.commands.append(command)
        if command == "show board":
            self.board_index += 1
            return f"Position ID: P{self.board_index}\nMatch ID: M{self.board_index}\n"
        if command.startswith("save match "):
            path = Path(command.removeprefix("save match "))
            path.write_text("sgf\n", encoding="utf-8")
        if command.startswith("export match text "):
            path = Path(command.removeprefix("export match text "))
            path.write_text("text\n", encoding="utf-8")
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
                position(0, "player_1", "none", None),
                position(0, "player_0", "none", (3, 1)),
                position(0, "player_1", "double", None),
                position(0, "player_0", "resignation", None),
                position(7, "player_0", "none", None),
            ]
        )
        self.analysis_calls: list[tuple[str, str]] = []

    def position_from_gnuid(self, gnuid: str) -> object:
        assert gnuid.startswith("P")
        return next(self.positions)

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
    assert engine_kit.analysis_calls == [("gnu", "cube"), ("sage", "checker"), ("gnu", "cube")]
    assert FakeBoard.last is not None
    assert [command for command in FakeBoard.last.commands if command in {"roll", "double", "take", "pass", "13/8", "accept"}] == [
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
