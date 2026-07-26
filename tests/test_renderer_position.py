import hashlib
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from backgammon_engine_kit import (
    BackgammonView,
    RendererPositionError,
    create_renderer_position,
    default_backgammon_view,
    renderer_position_from_gnuid,
    renderer_position_from_xgid,
    renderer_position_json,
    semantic_state_hash,
    view_hash,
)
from backgammon_engine_kit.position_contract import (
    validate_backgammon_view,
    validate_universal_position,
)


ROOT = Path(__file__).resolve().parents[1]
XGID_A = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
GNU_A = "PAAAICMAAAAAAA:cAkAAAAAAAAE"
OPENING_BOARD = "-b----E-C---eE---c-e----B-"
X_CONTEXT = {
    "cube": {"enabled": True},
    "rules": {"variation": "standard", "automatic_doubles": 0},
}
GNU_CONTEXT = {
    "cube": {"enabled": True},
    "rules": {
        "variation": "standard",
        "jacoby": False,
        "beavers": False,
        "automatic_doubles": 0,
        "maximum_cube": 1024,
    },
}


def _xgid(
    board=OPENING_BOARD,
    cube_exp=0,
    cube_owner=0,
    turn=1,
    action="00",
    score_bottom=0,
    score_top=0,
    rule=0,
    match_length=0,
    maximum_cube_exp=10,
):
    return "XGID={}:{}:{}:{}:{}:{}:{}:{}:{}:{}".format(
        board,
        cube_exp,
        cube_owner,
        turn,
        action,
        score_bottom,
        score_top,
        rule,
        match_length,
        maximum_cube_exp,
    )


def _command(*arguments):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "backgammon_engine_kit"] + list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=env,
    )


def test_validated_universal_position_to_immutable_renderer_position():
    source_result = renderer_position_from_xgid(XGID_A)
    explicit_view = BackgammonView(
        top_player="player_1",
        bottom_player="player_0",
        point_labels_for="player_1",
        bottom_home_board_side="left",
        cube_display_side="right",
        rotation="rotated",
        view_origin="external",
    )
    result = create_renderer_position(source_result.position, explicit_view)

    assert validate_universal_position(result.position) is result.position
    assert validate_backgammon_view(result.view) is result.view
    assert result.semantic_state_hash == semantic_state_hash(result.position)
    assert result.view_hash == view_hash(result.view)
    with pytest.raises(FrozenInstanceError):
        result.view_hash = "0" * 64


def test_renderer_position_rejects_hash_object_mismatch():
    result = renderer_position_from_xgid(XGID_A)
    with pytest.raises(RendererPositionError, match="semantic_state_hash"):
        replace(result, semantic_state_hash="0" * 64)
    with pytest.raises(RendererPositionError, match="view_hash"):
        replace(result, view_hash="0" * 64)


def test_xgid_uses_accepted_source_view_and_gnu_uses_generated_default():
    xgid = renderer_position_from_xgid(XGID_A)
    gnuid = renderer_position_from_gnuid(GNU_A)

    assert xgid.view.rotation == "source"
    assert xgid.view.view_origin == "source"
    assert gnuid.view == default_backgammon_view()
    assert gnuid.view.rotation == "default"
    assert gnuid.view.view_origin == "generated_default"


def test_equivalent_enriched_sources_have_same_position_and_semantic_hash():
    view = default_backgammon_view()
    xgid = renderer_position_from_xgid(
        XGID_A,
        view=view,
        external_settings=X_CONTEXT,
    )
    gnuid = renderer_position_from_gnuid(
        GNU_A,
        view=view,
        external_settings=GNU_CONTEXT,
    )

    assert xgid.position == gnuid.position
    assert xgid.semantic_state_hash == gnuid.semantic_state_hash
    assert xgid.view == gnuid.view
    assert xgid.view_hash == gnuid.view_hash


def test_orientation_changes_only_view_and_view_hash():
    original = renderer_position_from_xgid(XGID_A)
    rotated_view = replace(
        original.view,
        top_player="player_1",
        bottom_player="player_0",
        point_labels_for="player_1",
        bottom_home_board_side="left",
        cube_display_side="right",
        rotation="rotated",
        view_origin="external",
    )
    rotated = create_renderer_position(original.position, rotated_view)

    assert rotated.position == original.position
    assert rotated.semantic_state_hash == original.semantic_state_hash
    assert rotated.view != original.view
    assert rotated.view_hash != original.view_hash


def test_renderer_json_is_deterministic_and_preserves_separate_contracts():
    first = renderer_position_from_xgid(XGID_A)
    second = renderer_position_from_xgid(XGID_A)
    first_json = renderer_position_json(first)

    assert first == second
    assert first_json == renderer_position_json(second)
    assert "\n" not in first_json
    value = json.loads(first_json)
    assert set(value) == {
        "position",
        "semantic_state_hash",
        "view",
        "view_hash",
    }
    assert value["position"] == first.position.to_dict()
    assert value["view"] == first.view.to_dict()
    validate_universal_position(first.position)
    validate_backgammon_view(first.view)


@pytest.mark.parametrize(
    "identifier,expected",
    [
        (_xgid(turn=1, action="00"), {"on_roll": "player_1", "dice": None}),
        (_xgid(turn=-1, action="31"), {"on_roll": "player_0", "dice": (3, 1)}),
        (
            _xgid(board="a" + ("-" * 24) + "B", action="42"),
            {"bar_0": 1, "bar_1": 2, "off_0": 14, "off_1": 13},
        ),
        (
            _xgid(board="-" + "O" + ("-" * 22) + "o" + "-"),
            {"stack_0": 15, "stack_1": 15},
        ),
        (_xgid(cube_owner=0), {"cube_owner": "center"}),
        (_xgid(cube_exp=1, cube_owner=-1), {"cube_owner": "player_0"}),
        (_xgid(cube_exp=1, cube_owner=1), {"cube_owner": "player_1"}),
        (_xgid(match_length=0), {"match_length": 0}),
        (
            _xgid(score_bottom=2, score_top=4, match_length=7),
            {"score_0": 4, "score_1": 2, "match_length": 7},
        ),
        (
            _xgid(score_top=6, rule=1, match_length=7),
            {"crawford": True},
        ),
    ],
)
def test_renderer_exposes_supported_drawing_facts(identifier, expected):
    result = renderer_position_from_xgid(identifier)
    position = result.position
    validate_universal_position(position)
    validate_backgammon_view(result.view)

    actual = {
        "on_roll": position.state.on_roll,
        "dice": position.state.dice,
        "bar_0": position.board.player_0.bar,
        "bar_1": position.board.player_1.bar,
        "off_0": position.board.player_0.off,
        "off_1": position.board.player_1.off,
        "stack_0": max(position.board.player_0.points),
        "stack_1": max(position.board.player_1.points),
        "cube_owner": position.cube.owner,
        "score_0": position.score.player_0,
        "score_1": position.score.player_1,
        "match_length": position.score.match_length,
        "crawford": position.rules.crawford,
    }
    for key, value in expected.items():
        assert actual[key] == value


def test_inconsistent_position_and_schema_invalid_view_fail_clearly():
    result = renderer_position_from_xgid(XGID_A)
    invalid_board = replace(
        result.position.board,
        checker_count=replace(
            result.position.board.checker_count,
            player_0=16,
        ),
    )
    with pytest.raises(ValueError, match="checker total"):
        create_renderer_position(
            replace(result.position, board=invalid_board),
            result.view,
        )

    invalid_view = replace(result.view, bottom_player=result.view.top_player)
    with pytest.raises(ValueError, match="must differ"):
        create_renderer_position(result.position, invalid_view)


@pytest.mark.parametrize(
    "factory,identifier",
    [
        (renderer_position_from_xgid, "XGID=bad"),
        (renderer_position_from_xgid, "unsupported"),
        (renderer_position_from_gnuid, "PAAAICMAAAAAAA"),
        (renderer_position_from_gnuid, "PAAAICMAAAAAAA:bad"),
    ],
)
def test_bad_identifiers_fail(factory, identifier):
    with pytest.raises(ValueError):
        factory(identifier)


def test_accepted_schema_bytes_are_unchanged():
    expected = {
        "backgammon-view-v1.schema.json": (
            "7cf9b2ef78232670819e2d28a57f514246f6dd3840abc6c60ec3bb67ff2968af"
        ),
        "position-source-v1.schema.json": (
            "174145916b5032198064b023716f88165d1b66809a2a3008251e1cd3246c8c96"
        ),
        "universal-position-v1.schema.json": (
            "67fd6e7566e5720b4999c80b4e4ea52c93f370e1104dd59b8f1a4bcafd321a75"
        ),
    }
    schema_dir = (
        ROOT
        / "src"
        / "backgammon_engine_kit"
        / "position_contract"
        / "schemas"
    )
    actual = {
        path.name: hashlib.sha256(
            path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        for path in schema_dir.glob("*.json")
    }
    assert actual == expected


def test_renderer_cli_xgid_and_gnu_success_are_json_only_and_repeatable():
    for command, identifier in (
        ("render-xgid", XGID_A),
        ("render-gnuid", GNU_A),
    ):
        first = _command(command, identifier)
        second = _command(command, identifier)
        assert first.returncode == 0
        assert first.stderr == b""
        assert first.stdout == second.stdout
        assert first.stdout.endswith(b"\n")
        assert not first.stdout.endswith(b"\n\n")
        assert b"\r" not in first.stdout
        value = json.loads(first.stdout.decode("utf-8"))
        assert set(value) == {
            "position",
            "semantic_state_hash",
            "view",
            "view_hash",
        }


def test_renderer_cli_failure_uses_stderr_nonzero_and_empty_stdout():
    result = _command("render-xgid", "XGID=bad")
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"error:" in result.stderr
    assert b"XGID" in result.stderr


def test_renderer_cli_help_documents_both_identifier_families_and_examples():
    result = _command("--help")
    assert result.returncode == 0
    assert b"render-xgid" in result.stdout
    assert b"render-gnuid" in result.stdout
    assert b"Examples:" in result.stdout
