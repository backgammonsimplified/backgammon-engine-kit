from __future__ import annotations

import json
from pathlib import Path

from runner.sage_gnu_campaign.dice import (
    SeatDiceController,
    dice_record,
    namespace_seed,
    stream_id,
)


def controller(tmp_path: Path, side: str) -> SeatDiceController:
    mapping = {"O": "sage", "X": "gnu"} if side == "A" else {"O": "gnu", "X": "sage"}
    return SeatDiceController(tmp_path / side, "base", side, 5, 2, mapping)


def test_historical_random_key_record_is_exact() -> None:
    assert dice_record("base:match:A", 1, 7, 3, "O", 11) == {
        "roll_index": 11,
        "opening_die": 1,
        "die1": 2,
        "die2": 3,
    }


def test_streams_and_consumption_attach_to_physical_seat(tmp_path: Path) -> None:
    source = controller(tmp_path, "A")
    source.prepare_files()
    source.prepare_opening(1)
    source.opening_dice()
    source.prepare_after_turn(1, "O")
    source.checker_dice("X")
    manifest_path, consumption_path = source.write_evidence()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    consumption = [json.loads(line) for line in consumption_path.read_text(encoding="utf-8").splitlines()]
    assert manifest["engine_by_physical_seat"] == {"O": "sage", "X": "gnu"}
    assert {entry["physical_seat"] for entry in consumption[:2]} == {"O", "X"}
    assert consumption[-1]["physical_seat"] == "X"
    assert consumption[-1]["engine"] == "gnu"
    assert consumption[-1]["stream_id"] == stream_id(namespace_seed("base", "A"), 1, "X")


def test_a_b_namespaces_diverge_without_stream_identity_drift(tmp_path: Path) -> None:
    match_a = controller(tmp_path, "A")
    match_b = controller(tmp_path, "B")
    for item in (match_a, match_b):
        item.prepare_files()
        item.prepare_opening(1)
        item.opening_dice()
    a_o_id = stream_id(match_a.seed, 1, "O")
    b_o_id = stream_id(match_b.seed, 1, "O")
    match_a.checker_dice("O")
    match_a.checker_dice("O")
    match_b.checker_dice("X")
    assert match_a.checker_roll_index[(1, "O")] == 2
    assert match_b.checker_roll_index[(1, "X")] == 1
    assert stream_id(match_a.seed, 1, "O") == a_o_id
    assert stream_id(match_b.seed, 1, "O") == b_o_id
    assert a_o_id != b_o_id
