"""Focused tests for the native XGID and GNUID codec."""

import base64

import pytest

from backgammon_engine_kit.position_contract import (
    NativeIdentifierCodecError,
    convert_gnuid_to_xgid,
    convert_xgid_to_gnuid,
    gnuid_from_position,
    gnuid_to_xgid,
    match_id_from_position,
    position_from_gnuid,
    position_from_xgid,
    position_id_from_position,
    xgid_to_gnuid,
)


CRASH_XGID = "XGID=-BDB-------------a------e-:1:-1:-1:42:0:0:0:5:8"
CRASH_GNU_CANONICAL = "ewMAAD4gAAAAAA:AQGqAAAAAAAE"
CRASH_GNU_DIFFERENT_PLAYER_STATE = "ewMAAD4gAAAAAA:UQmqAAAAAAAE"
CRASH_XGID_NORMALIZED_MAX = "XGID=-BDB-------------a------e-:1:-1:-1:42:0:0:0:5:10"
CRASH_UQ_XGID = "XGID=-E------A-------------bdb-:1:1:1:42:0:0:0:5:10"

CALCULATOR_GNU = "4HPwATDgc/ABMA:8IhuACAACAAE"
CALCULATOR_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:1:53:1:2:1:3:10"
CALCULATOR_NORMALIZED_GNU = "4HPwATDgc/ABMA:8IluACAACAAE"


@pytest.mark.parametrize(
    "xgid,expected",
    [
        (
            "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10",
            "PAAAICMAAAAAAA:cAkAAAAAAAAE",
        ),
        (
            "XGID=---D---------------a--b-a-:0:0:-1:00:0:0:0:0:8",
            "PAAAICMAAAAAAA:MAEAAAAAAAAE",
        ),
        (
            "XGID=-a-a--E-C---dE---d-e----B-:0:0:1:42:0:0:0:0:8",
            "4PPgASTgc/ABMA:cAkKAAAAAAAE",
        ),
        (
            "XGID=-b----E-C---eE---c-e----B-:1:1:1:00:0:0:0:0:10",
            "4HPwATDgc/ABMA:UQkAAAAAAAAE",
        ),
        (
            "XGID=-b----E-C---eE---c-e----B-:1:-1:1:00:0:0:0:0:10",
            "4HPwATDgc/ABMA:QQkAAAAAAAAE",
        ),
        (
            "XGID=-b----E-C---eE---c-e----B-:0:0:1:00:2:4:0:7:10",
            "4HPwATDgc/ABMA:cAngAEAAEAAE",
        ),
    ],
)
def test_canonical_stable_player_conversion_matches_retained_gnu_vectors(xgid, expected):
    assert xgid_to_gnuid(xgid, allow_lossy=True) == expected


def test_known_top_roller_xgid_requires_only_maximum_cube_loss():
    with pytest.raises(NativeIdentifierCodecError, match="maximum-cube"):
        xgid_to_gnuid(CRASH_XGID)

    converted = convert_xgid_to_gnuid(CRASH_XGID, allow_lossy=True)
    assert converted.identifier == CRASH_GNU_CANONICAL
    assert converted.position.state.on_roll == "player_0"
    assert converted.position.state.decision_player == "player_0"
    assert converted.position.cube.owner == "player_0"
    assert converted.position.score.player_0 == 0
    assert converted.position.score.player_1 == 0
    assert [loss.field for loss in converted.losses] == ["/rules/maximum_cube"]


def test_aq_and_uq_are_different_stable_player_states():
    aq = position_from_gnuid(CRASH_GNU_CANONICAL)
    uq = position_from_gnuid(CRASH_GNU_DIFFERENT_PLAYER_STATE)

    assert aq.state.on_roll == aq.state.decision_player == "player_0"
    assert aq.cube.owner == "player_0"
    assert uq.state.on_roll == uq.state.decision_player == "player_1"
    assert uq.cube.owner == "player_1"
    assert aq.board.player_0 != uq.board.player_0
    assert aq.board.player_1 != uq.board.player_1
    assert gnuid_to_xgid(CRASH_GNU_CANONICAL) == CRASH_XGID_NORMALIZED_MAX
    assert gnuid_to_xgid(CRASH_GNU_DIFFERENT_PLAYER_STATE) == CRASH_UQ_XGID


def test_same_position_id_needs_match_id_dice_owner_for_stable_checker_ownership():
    position_id = CRASH_GNU_CANONICAL.split(":", 1)[0]
    aq = position_from_gnuid(position_id + ":AQGqAAAAAAAE")
    uq = position_from_gnuid(position_id + ":UQmqAAAAAAAE")
    assert aq.state.on_roll == "player_0"
    assert uq.state.on_roll == "player_1"
    assert aq.board.player_0 == uq.board.player_1
    assert aq.board.player_1 == uq.board.player_0


def test_backgammoncalculator_reference_vector_matches_both_directions():
    assert gnuid_to_xgid(CALCULATOR_GNU) == CALCULATOR_XGID
    assert xgid_to_gnuid(CALCULATOR_XGID) == CALCULATOR_NORMALIZED_GNU


def test_gnuid_xgid_gnuid_reports_lifecycle_normalization():
    converted = convert_gnuid_to_xgid(CALCULATOR_GNU)
    assert converted.identifier == CALCULATOR_XGID
    assert any(change.field == "/state/game_state" for change in converted.normalizations)
    assert xgid_to_gnuid(converted.identifier) == CALCULATOR_NORMALIZED_GNU


@pytest.mark.parametrize(
    "gnuid",
    [
        "4PPgASTgc/ABMA:cAnqAAAAAAAE",
        "4HPhASLgc/ABMA:cAnnAAAAAAAE",
        "eM4NgANs3oGDAA:cAngAAAAAAAE",
        "eM4NgANs3oGDAA:cBHgAAAAAAAE",
        "3HsHAgD1PQ8AAA:QYnuAAAAAAAE",
        "jGfwATDgc/ABMA:cAngAGAAKAAE",
    ],
)
def test_supported_playing_gnuids_reencode_exactly(gnuid):
    position = position_from_gnuid(gnuid)
    assert gnuid_from_position(position) == gnuid
    position_id, match_id = gnuid.split(":", 1)
    assert position_id_from_position(position) == position_id
    assert match_id_from_position(position) == match_id


def test_xgid_native_parse_and_encode_round_trip_is_exact_for_represented_fields():
    source = "XGID=-b----E-C---eE---c-e----B-:0:0:1:53:1:2:1:3:10"
    position = position_from_xgid(source)
    # This vector has all XGID-specific fields represented in canonical state,
    # including maximum cube and money-game rule bits.
    from backgammon_engine_kit.position_contract import xgid_from_position

    assert xgid_from_position(position) == source


def test_match_id_bit_66_is_inverse_jacoby_not_a_fixed_framing_bit():
    no_jacoby = position_from_xgid(
        "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
    )
    jacoby = position_from_xgid(
        "XGID=-A-B--A---------------d---:0:0:1:00:0:0:1:0:10"
    )

    no_jacoby_mid = match_id_from_position(no_jacoby)
    jacoby_mid = match_id_from_position(jacoby)
    no_jacoby_bytes = base64.b64decode(no_jacoby_mid)
    jacoby_bytes = base64.b64decode(jacoby_mid)

    assert ((no_jacoby_bytes[8] >> 2) & 1) == 1
    assert ((jacoby_bytes[8] >> 2) & 1) == 0
    assert position_from_gnuid(
        position_id_from_position(jacoby) + ":" + jacoby_mid
    ).rules.jacoby is True


def test_native_decoder_accepts_jacoby_match_id_that_legacy_decoder_rejects():
    source = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:1:0:10"
    gnuid = xgid_to_gnuid(source)
    assert position_from_gnuid(gnuid).rules.jacoby is True



@pytest.mark.parametrize(
    "xgid,dice_owner,turn_owner",
    [
        (
            "XGID=-b----E-C---eE---c-e----B-:1:-1:-1:D:1:3:0:7:10",
            "player_0",
            "player_1",
        ),
        (
            "XGID=-b----E-C---eE---c-e----B-:1:1:1:D:1:3:0:7:10",
            "player_1",
            "player_0",
        ),
    ],
)
def test_pending_double_turn_is_doubler_and_decision_owner_is_responder(
    xgid, dice_owner, turn_owner
):
    source = position_from_xgid(xgid)
    assert source.state.on_roll == dice_owner
    assert source.state.decision_player == turn_owner
    assert source.cube.pending_action.offerer == dice_owner
    assert source.cube.pending_action.responder == turn_owner

    gnuid = xgid_to_gnuid(xgid)
    reparsed = position_from_gnuid(gnuid)
    assert reparsed.state.on_roll == dice_owner
    assert reparsed.state.decision_player == turn_owner
    assert reparsed.cube.pending_action.offerer == dice_owner
    assert reparsed.cube.pending_action.responder == turn_owner
    assert gnuid_to_xgid(gnuid) == xgid

def test_beaver_and_raccoon_are_rejected_by_strict_gnu_encoding():
    board = "-A-B--A---------------d---"
    for action in ("B", "R"):
        with pytest.raises(NativeIdentifierCodecError, match="cannot represent"):
            xgid_to_gnuid(
                "XGID={}:1:-1:1:{}:0:0:2:0:10".format(board, action)
            )
