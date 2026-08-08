"""Focused stable-player identifier-bridge coverage.

Fixture provenance:
- CHECKER_GNUID and the original cube board come from the repository's
  committed GNU 1.08.003 evidence bundles.
- COMPLETE_CUBE_GNUID and both XGIDs were produced with AnkiGammon 1.7.0
  parse/encode round trips. Engine Kit deliberately does not inherit
  AnkiGammon's turn-dependent checker ownership labels.
"""

import base64

import pytest

from ankigammon.models import CubeState, Player
from ankigammon.utils.gnuid import encode_gnuid, parse_gnuid
from ankigammon.utils.xgid import encode_xgid, parse_xgid

from backgammon_engine_kit import (
    UnsupportedAnalysisIdentifier,
    detect_identifier_format,
    parse_analysis_identifier,
    to_gnu_request,
    to_sage_request,
)
from backgammon_engine_kit.position_contract import (
    canonical_to_bgsage,
    decode_gnuid,
    decode_xgid,
    enrich_position,
)
from backgammon_engine_kit.sage.invocation import canonical_position_context


CHECKER_GNUID = "4PPgASTgc/ABMA:cAnqAAAAAAAE"
COMPLETE_CUBE_GNUID = "bD3BAQyYd2cEAA:ggHgAEAAEAAE"
RET_001_SETUP_CHECKER_GNUID = "4HPwATDgc/ABMA:MADuAAAAAAAE"
RET_002_CHECKER_GNUID = "4HPhASLgc/ABMA:cAnnAAAAAAAE"
RET_003_PLAYING_CUBE_GNUID = "eM4NgANs3oGDAA:cAngAAAAAAAE"
# Synthetic setup-state cube identifier derived from RET-003 by changing only
# GNU GameState from playing (001) to the no-game-started code (000).
SYNTHETIC_RET_003_SETUP_CUBE_GNUID = "eM4NgANs3oGDAA:cAjgAAAAAAAE"
RET_004_PENDING_DOUBLE_GNUID = "eM4NgANs3oGDAA:cBHgAAAAAAAE"
RET_005_CHECKER_GNUID = "4NvBCSCYc8MBUA:MAHqAAAAAAAE"
RET_006_CHECKER_GNUID = "3HsHAgD1PQ8AAA:QYnuAAAAAAAE"
RET_010_POST_CRAWFORD_CUBE_GNUID = "jGfwATDgc/ABMA:cAngAGAAKAAE"
POSITION_ID = "PAAAICMAAAAAAA"
GNU_BOTTOM_ON_ROLL = "PAAAICMAAAAAAA:cAkAAAAAAAAE"
GNU_TOP_ON_ROLL = "PAAAICMAAAAAAA:MAEAAAAAAAAE"
XGID_TOP = "XGID=---D---------------a--b-a-:0:0:-1:00:0:0:0:0:10"
XGID_BOTTOM = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
AUTHENTIC_XGID_TOP = "XGID=---D---------------a--b-a-:0:0:-1:00:0:0:0:0:8"
XGID_CUBE = "XGID=---bB-DCC-B-cA---a-dabb---:2:1:1:00:4:2:1:7:10"
AUTHENTIC_CRAWFORD_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:1:00:2:6:1:7:10"
# Synthetic derivative of AUTHENTIC_CRAWFORD_XGID: only dice changes from
# pre-roll 00 to 42, proving Crawford does not prohibit checker decisions.
SYNTHETIC_DICE_BEARING_CRAWFORD_XGID = (
    "XGID=-b----E-C---eE---c-e----B-:0:0:1:42:2:6:1:7:10"
)
AUTHENTIC_NON_CRAWFORD_CUBE_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:1:00:0:0:0:0:8"
AUTHENTIC_PENDING_DOUBLE_XGID = "XGID=-b----E-C---eE---c-e----B-:1:1:1:D:0:0:0:0:10"
KNOWN_MISMATCH_TOP_XGID = "XGID=-BDB-------------a------e-:1:-1:-1:42:0:0:0:5:8"
KNOWN_MISMATCH_TOP_GNUID = "ewMAAD4gAAAAAA:AQGqAAAAAAAE"
MATCH_7_ASYMMETRIC_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:1:00:2:4:0:7:10"
MATCH_7_ASYMMETRIC_GNUID = "4HPwATDgc/ABMA:cAngAEAAEAAE"
CUBE_OWNED_O_XGID = "XGID=-b----E-C---eE---c-e----B-:1:1:1:00:0:0:0:0:10"
CUBE_OWNED_O_GNUID = "4HPwATDgc/ABMA:UQkAAAAAAAAE"
CUBE_OWNED_X_XGID = "XGID=-b----E-C---eE---c-e----B-:1:-1:1:00:0:0:0:0:10"
CUBE_OWNED_X_GNUID = "4HPwATDgc/ABMA:QQkAAAAAAAAE"


def _direct_gnu_encode(position, metadata, *, only_position=False):
    return encode_gnuid(
        position,
        cube_value=metadata.get("cube_value", 1),
        cube_owner=metadata.get("cube_owner", CubeState.CENTERED),
        dice=metadata.get("dice"),
        on_roll=metadata.get("on_roll", Player.X),
        score_x=metadata.get("score_x", 0),
        score_o=metadata.get("score_o", 0),
        match_length=metadata.get("match_length", 0),
        crawford=metadata.get("crawford", False),
        only_position=only_position,
    )


def _with_game_state(raw_identifier, game_state):
    position_id, match_id = raw_identifier.split(":", 1)
    raw_match = bytearray(base64.b64decode(match_id + "="))
    raw_match[1] = (raw_match[1] & ~0x07) | game_state
    encoded = base64.b64encode(bytes(raw_match)).decode("ascii").rstrip("=")
    return position_id + ":" + encoded


def _stable_checker_ownership(parsed):
    points = parsed.canonical_position.points
    return {
        "player_0_points": tuple(max(value, 0) for value in reversed(points[1:25])),
        "player_1_points": tuple(max(-value, 0) for value in points[1:25]),
        "player_0_bar": max(points[0], 0),
        "player_1_bar": max(-points[25], 0),
        "player_0_off": parsed.canonical_position.x_off,
        "player_1_off": parsed.canonical_position.o_off,
    }


def _assert_stable_board(raw_identifier, player_0_points, player_1_points, *, bars=(0, 0), off=(0, 0)):
    parsed = parse_analysis_identifier(raw_identifier)
    actual = _stable_checker_ownership(parsed)
    assert actual == {
        "player_0_points": tuple(player_0_points),
        "player_1_points": tuple(player_1_points),
        "player_0_bar": bars[0],
        "player_1_bar": bars[1],
        "player_0_off": off[0],
        "player_1_off": off[1],
    }

    contract = decode_gnuid(raw_identifier).position.board
    assert actual["player_0_points"] == contract.player_0.points
    assert actual["player_1_points"] == contract.player_1.points
    assert (actual["player_0_bar"], actual["player_1_bar"]) == (
        contract.player_0.bar,
        contract.player_1.bar,
    )
    assert (actual["player_0_off"], actual["player_1_off"]) == (
        contract.player_0.off,
        contract.player_1.off,
    )


def _analysis_position(raw_identifier):
    decoded = decode_gnuid(raw_identifier)
    position, _ = enrich_position(
        decoded.position,
        decoded.source,
        {
            "cube": {"enabled": True},
            "rules": {"variation": "standard", "jacoby": False, "beavers": False},
        },
    )
    return position


def test_detection_prioritizes_complete_gnuid_then_position_id_then_xgid():
    assert detect_identifier_format(CHECKER_GNUID) == "complete_gnuid"
    assert detect_identifier_format(POSITION_ID) == "position_id"
    assert detect_identifier_format(XGID_TOP) == "xgid"
    assert detect_identifier_format("anything:with-a-colon") == "invalid_or_unsupported"
    assert detect_identifier_format("XGID=bad:colon") == "invalid_or_unsupported"


def test_complete_gnuid_checker_preserves_raw_dice_and_native_metadata():
    parsed = parse_analysis_identifier(CHECKER_GNUID)
    assert parsed.raw_identifier == CHECKER_GNUID
    assert parsed.identifier_format == "complete_gnuid"
    assert parsed.state.dice == (4, 2)
    assert parsed.state.match_length == 7
    assert parsed.native_metadata["on_roll"] == "X"
    assert parsed.match_id == CHECKER_GNUID.split(":", 1)[1]


def test_complete_gnuid_cube_preserves_match_id_state():
    parsed = parse_analysis_identifier(COMPLETE_CUBE_GNUID)
    assert parsed.state.dice is None
    assert parsed.state.availability["dice"] == "available"
    assert parsed.state.cube_value == 4
    assert parsed.state.cube_owner == "player_x"
    assert parsed.state.score_x == 4
    assert parsed.state.score_o == 2
    assert parsed.state.match_length == 7
    assert parsed.state.crawford is True


def test_ret_001_setup_state_checker_is_ready_without_remapping_game_state():
    parsed = parse_analysis_identifier(RET_001_SETUP_CHECKER_GNUID)
    assert parsed.state.game_state == 0
    assert parsed.state.availability["game_state"] == "available"
    assert parsed.native_metadata["game_state"] == 0
    assert parsed.unsupported_state == ()

    gnu = to_gnu_request(parsed, "checker")
    assert gnu.ready
    assert gnu.request.position.id == RET_001_SETUP_CHECKER_GNUID
    assert gnu.request.dice == (4, 3)
    assert gnu.identifier_provenance == "original_complete_gnuid"
    assert gnu.conversion_applied is False
    assert gnu.canonical_request.identifier.raw_identifier == RET_001_SETUP_CHECKER_GNUID
    assert gnu.canonical_request.state.game_state == 0


def test_synthetic_ret_003_derived_setup_state_cube_is_ready_for_both_request_paths():
    for prepare in (to_gnu_request, to_sage_request):
        prepared = prepare(SYNTHETIC_RET_003_SETUP_CUBE_GNUID, "cube")
        assert prepared.ready
        assert prepared.request.dice is None
        assert prepared.engine_identifier == SYNTHETIC_RET_003_SETUP_CUBE_GNUID
        assert prepared.canonical_request.state.game_state == 0
        assert prepared.canonical_request.identifier.native_metadata["game_state"] == 0
        assert prepared.semantic_equivalence_claimed is False

    sage = to_sage_request(SYNTHETIC_RET_003_SETUP_CUBE_GNUID, "cube")
    assert sage.identifier_provenance == "canonical_reencoding_from_complete_gnuid"


def test_setup_state_sage_canonical_reencoding_preserves_game_state_code():
    prepared = to_sage_request(RET_001_SETUP_CHECKER_GNUID, "checker")
    assert prepared.ready
    assert prepared.identifier_provenance == "canonical_reencoding_from_complete_gnuid"
    _, metadata = parse_gnuid(prepared.engine_identifier)
    assert metadata["game_state"] == 0


def test_playing_state_request_compatibility_is_unchanged():
    for prepare in (to_gnu_request, to_sage_request):
        checker = prepare(CHECKER_GNUID, "checker")
        cube = prepare(RET_003_PLAYING_CUBE_GNUID, "cube")
        assert checker.ready and checker.canonical_request.state.game_state == 1
        assert cube.ready and cube.canonical_request.state.game_state == 1


@pytest.mark.parametrize("game_state", [2, 3, 4])
def test_finished_resigned_and_dropped_game_state_codes_remain_fail_closed(game_state):
    raw = _with_game_state(CHECKER_GNUID, game_state)
    parsed = parse_analysis_identifier(raw)
    assert parsed.state.game_state == game_state
    assert parsed.state.availability["game_state"] == "unsupported"
    assert "non_playing_game_state" in parsed.unsupported_state
    for prepare in (to_gnu_request, to_sage_request):
        prepared = prepare(parsed, "checker")
        assert prepared.status == "unsupported"
        assert prepared.request is None


def test_ret_004_pending_offer_remains_explicit_and_fail_closed():
    parsed = parse_analysis_identifier(RET_004_PENDING_DOUBLE_GNUID)
    assert parsed.native_metadata["doubled"] is True
    assert parsed.normalization_applied is False
    assert parsed.source_turn == "player_o"
    assert parsed.canonical_player_mapping == {
        "player_x": "AnkiGammon Player.X; top; positive checkers",
        "player_o": "AnkiGammon Player.O; bottom; negative checkers",
    }
    assert parsed.source_player_mapping == {
        "GNU player 0 / XGID top / X": "player_x",
        "GNU player 1 / XGID bottom / O": "player_o",
    }
    _assert_stable_board(
        RET_004_PENDING_DOUBLE_GNUID,
        (0, 0, 0, 4, 0, 3, 0, 3, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0),
        (0, 0, 2, 2, 0, 4, 3, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0),
    )
    for prepare in (to_gnu_request, to_sage_request):
        prepared = prepare(parsed, "cube")
        assert prepared.status == "unsupported"
        assert "pending_double" in prepared.unsupported_state
        assert prepared.request is None


def test_ret_002_bottom_o_on_roll_checker_uses_stable_o_x_ownership():
    parsed = parse_analysis_identifier(RET_002_CHECKER_GNUID)
    assert parsed.state.on_roll == "player_o"
    assert parsed.normalization_applied is False
    assert parsed.source_player_mapping == {
        "GNU player 0 / XGID top / X": "player_x",
        "GNU player 1 / XGID bottom / O": "player_o",
    }
    _assert_stable_board(
        RET_002_CHECKER_GNUID,
        (0, 0, 0, 0, 0, 5, 0, 3, 1, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1),
        (0, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2),
    )


def test_ret_003_bottom_o_on_roll_cube_uses_stable_board_and_centered_cube():
    parsed = parse_analysis_identifier(RET_003_PLAYING_CUBE_GNUID)
    assert parsed.state.on_roll == "player_o"
    assert parsed.state.cube_owner == "centered"
    _assert_stable_board(
        RET_003_PLAYING_CUBE_GNUID,
        (0, 0, 0, 4, 0, 3, 0, 3, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0),
        (0, 0, 2, 2, 0, 4, 3, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0),
    )


def test_ret_006_asymmetric_board_cube_owner_and_off_counts_are_stable():
    parsed = parse_analysis_identifier(RET_006_CHECKER_GNUID)
    assert parsed.state.cube_value == 2
    assert parsed.state.cube_owner == "player_x"
    _assert_stable_board(
        RET_006_CHECKER_GNUID,
        (0, 0, 3, 4, 4, 3, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (1, 1, 5, 4, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        off=(0, 0),
    )


def test_ret_010_asymmetric_post_crawford_board_is_stable():
    parsed = parse_analysis_identifier(RET_010_POST_CRAWFORD_CUBE_GNUID)
    assert parsed.state.score_x == 6
    assert parsed.state.score_o == 5
    assert parsed.state.crawford is False
    _assert_stable_board(
        RET_010_POST_CRAWFORD_CUBE_GNUID,
        (0, 0, 2, 0, 0, 4, 0, 2, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2),
        (0, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2),
    )


def test_ret_001_and_ret_005_top_x_on_roll_ownership_remains_unchanged():
    _assert_stable_board(
        RET_001_SETUP_CHECKER_GNUID,
        (0, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2),
        (0, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2),
    )
    _assert_stable_board(
        RET_005_CHECKER_GNUID,
        (0, 0, 0, 2, 0, 3, 0, 3, 2, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
        (0, 0, 0, 0, 0, 5, 2, 3, 0, 0, 0, 0, 3, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1),
        bars=(1, 0),
    )
    for raw_identifier in (RET_001_SETUP_CHECKER_GNUID, RET_005_CHECKER_GNUID):
        parsed = parse_analysis_identifier(raw_identifier)
        assert parsed.state.on_roll == "player_x"
        assert parsed.normalization_applied is True


def test_pending_resignation_remains_explicit_and_fail_closed():
    position_id, match_id = CHECKER_GNUID.split(":", 1)
    raw_match = bytearray(base64.b64decode(match_id + "="))
    raw_match[1] |= 1 << 5
    resignation_id = position_id + ":" + base64.b64encode(bytes(raw_match)).decode("ascii").rstrip("=")
    parsed = parse_analysis_identifier(resignation_id)
    assert parsed.native_metadata["resigned"] == 1
    assert "pending_resignation" in parsed.unsupported_state
    assert to_gnu_request(parsed, "checker").status == "unsupported"


def test_position_id_without_match_id_keeps_all_match_state_unavailable():
    parsed = parse_analysis_identifier(POSITION_ID)
    assert parsed.raw_identifier == POSITION_ID
    assert parsed.match_id is None
    for field in ("dice", "on_roll", "score_x", "score_o", "cube_value", "cube_owner", "match_length"):
        assert parsed.state.availability[field] == "unavailable"
        assert field in parsed.unavailable_state
    prepared = to_sage_request(POSITION_ID, "cube")
    assert prepared.status == "unavailable"
    assert prepared.request is None
    assert set(prepared.missing_state) >= {"dice", "on_roll", "cube_value", "match_length"}


def test_position_id_can_be_completed_only_with_explicit_state():
    prepared = to_sage_request(
        POSITION_ID,
        "cube",
        explicit_state={
            "on_roll": "X",
            "dice": None,
            "cube_value": 1,
            "cube_owner": "centered",
            "score_x": 0,
            "score_o": 0,
            "match_length": 0,
        },
    )
    assert prepared.ready
    assert prepared.identifier_provenance == "canonical_conversion_from_position_id_plus_explicit_state"
    assert prepared.canonical_request.state_provenance["on_roll"] == "explicit_state"


def test_complete_setup_state_rejects_contradictory_explicit_state():
    with pytest.raises(ValueError, match="explicit game_state contradicts identifier state"):
        to_gnu_request(
            RET_001_SETUP_CHECKER_GNUID,
            "checker",
            explicit_state={"game_state": 1},
        )


def test_cube_request_during_crawford_remains_fail_closed():
    prepared = to_gnu_request(COMPLETE_CUBE_GNUID, "cube")
    assert prepared.status == "unsupported"
    assert "cube_decision_illegal_during_crawford" in prepared.unsupported_state
    assert prepared.request is None


@pytest.mark.parametrize("prepare", [to_gnu_request, to_sage_request])
def test_authentic_crawford_xgid_cube_request_is_unsupported_for_both_engines(prepare):
    parsed = parse_analysis_identifier(AUTHENTIC_CRAWFORD_XGID)
    assert parsed.raw_identifier == AUTHENTIC_CRAWFORD_XGID
    assert parsed.state.crawford is True
    assert parsed.state.availability["crawford"] == "available"

    prepared = prepare(parsed, "cube")
    assert prepared.status == "unsupported"
    assert "cube_decision_illegal_during_crawford" in prepared.unsupported_state
    assert prepared.canonical_request.identifier.raw_identifier == AUTHENTIC_CRAWFORD_XGID
    assert prepared.request is None


@pytest.mark.parametrize("prepare", [to_gnu_request, to_sage_request])
def test_synthetic_dice_bearing_crawford_checker_request_is_not_blocked(prepare):
    prepared = prepare(SYNTHETIC_DICE_BEARING_CRAWFORD_XGID, "checker")
    assert prepared.ready
    assert prepared.request.dice == (4, 2)
    assert prepared.canonical_request.state.crawford is True
    assert "cube_decision_illegal_during_crawford" not in prepared.unsupported_state


@pytest.mark.parametrize("prepare", [to_gnu_request, to_sage_request])
def test_authentic_non_crawford_xgid_cube_request_remains_ready(prepare):
    prepared = prepare(AUTHENTIC_NON_CRAWFORD_CUBE_XGID, "cube")
    assert prepared.ready
    assert prepared.canonical_request.state.crawford is None


@pytest.mark.parametrize("prepare", [to_gnu_request, to_sage_request])
def test_authentic_pending_double_xgid_remains_unsupported(prepare):
    prepared = prepare(AUTHENTIC_PENDING_DOUBLE_XGID, "cube")
    assert prepared.status == "unsupported"
    assert "pending_cube_action:D" in prepared.unsupported_state
    assert prepared.request is None


@pytest.mark.parametrize(
    "raw,on_roll,normalization,orientation",
    [
        (XGID_BOTTOM, "player_o", False, "xgid-fixed-top-x-bottom-o"),
        (XGID_TOP, "player_x", False, "xgid-fixed-top-x-bottom-o"),
    ],
)
def test_xgid_preserves_fixed_checker_ownership_for_both_players_on_roll(
    raw, on_roll, normalization, orientation
):
    parsed = parse_analysis_identifier(raw)
    assert parsed.raw_identifier == raw
    assert parsed.source_turn == on_roll
    assert parsed.source_orientation == orientation
    assert parsed.normalization_applied is normalization
    assert parsed.point_reversal_applied is normalization
    assert parsed.bar_reversal_applied is normalization
    assert parsed.source_player_mapping == {"top/X": "player_x", "bottom/O": "player_o"}
    assert "positive checkers" in parsed.canonical_player_mapping["player_x"]


def test_matching_gnuid_and_xgid_pairs_have_equal_stable_canonical_positions():
    assert parse_analysis_identifier(GNU_TOP_ON_ROLL).canonical_position == parse_analysis_identifier(XGID_TOP).canonical_position
    assert parse_analysis_identifier(GNU_BOTTOM_ON_ROLL).canonical_position == parse_analysis_identifier(XGID_BOTTOM).canonical_position
    assert parse_analysis_identifier(GNU_BOTTOM_ON_ROLL).canonical_position != parse_analysis_identifier(GNU_TOP_ON_ROLL).canonical_position


@pytest.mark.parametrize(
    "raw_xgid,expected_gnuid",
    [(XGID_BOTTOM, GNU_BOTTOM_ON_ROLL), (AUTHENTIC_XGID_TOP, GNU_TOP_ON_ROLL)],
)
def test_source_authentic_xgid_conversions_are_exact_and_physically_equal(raw_xgid, expected_gnuid):
    xgid = parse_analysis_identifier(raw_xgid)
    gnuid = parse_analysis_identifier(expected_gnuid)
    assert xgid.canonical_position == gnuid.canonical_position
    for prepare in (to_gnu_request, to_sage_request):
        prepared = prepare(raw_xgid, "cube")
        assert prepared.ready
        assert prepared.engine_identifier == expected_gnuid
        assert prepared.engine_identifier.split(":", 1) == expected_gnuid.split(":", 1)


@pytest.mark.parametrize(
    "raw_xgid,decision_type,expected_gnuid",
    [
        (KNOWN_MISMATCH_TOP_XGID, "checker", KNOWN_MISMATCH_TOP_GNUID),
        (MATCH_7_ASYMMETRIC_XGID, "cube", MATCH_7_ASYMMETRIC_GNUID),
        (CUBE_OWNED_O_XGID, "cube", CUBE_OWNED_O_GNUID),
        (CUBE_OWNED_X_XGID, "cube", CUBE_OWNED_X_GNUID),
    ],
)
def test_gallery_regressions_preserve_stable_player_state(
    raw_xgid, decision_type, expected_gnuid
):
    source = parse_analysis_identifier(raw_xgid)
    for prepare in (to_gnu_request, to_sage_request):
        prepared = prepare(raw_xgid, decision_type)
        assert prepared.ready
        assert prepared.engine_identifier == expected_gnuid
        converted = parse_analysis_identifier(prepared.engine_identifier)
        assert converted.canonical_position == source.canonical_position
        assert converted.state.on_roll == source.state.on_roll
        assert converted.state.dice == source.state.dice
        assert converted.state.cube_value == source.state.cube_value
        assert converted.state.cube_owner == source.state.cube_owner
        assert converted.state.score_x == source.state.score_x
        assert converted.state.score_o == source.state.score_o
        assert converted.state.match_length == source.state.match_length


def test_changing_only_xgid_turn_keeps_checker_ownership_stable():
    bottom_on_roll = XGID_BOTTOM
    top_on_roll = XGID_BOTTOM.replace(":0:0:1:00:", ":0:0:-1:00:")
    bottom = parse_analysis_identifier(bottom_on_roll)
    top = parse_analysis_identifier(top_on_roll)
    assert bottom.canonical_position == top.canonical_position
    assert bottom.state.on_roll == "player_o"
    assert top.state.on_roll == "player_x"

    bottom_gnu = to_sage_request(bottom_on_roll, "cube").engine_identifier
    top_gnu = to_sage_request(top_on_roll, "cube").engine_identifier
    assert bottom_gnu != top_gnu
    assert parse_analysis_identifier(bottom_gnu).canonical_position == bottom.canonical_position
    assert parse_analysis_identifier(top_gnu).canonical_position == top.canonical_position
    assert parse_analysis_identifier(bottom_gnu).state.on_roll == "player_o"
    assert parse_analysis_identifier(top_gnu).state.on_roll == "player_x"


@pytest.mark.parametrize(
    "raw_xgid",
    [
        XGID_BOTTOM,
        AUTHENTIC_XGID_TOP,
        MATCH_7_ASYMMETRIC_XGID,
        CUBE_OWNED_O_XGID,
        CUBE_OWNED_X_XGID,
    ],
)
def test_bridge_xgid_state_matches_independent_universal_decoder(raw_xgid):
    parsed = parse_analysis_identifier(raw_xgid)
    decoded = decode_xgid(raw_xgid).position
    actual = _stable_checker_ownership(parsed)
    assert actual["player_0_points"] == decoded.board.player_0.points
    assert actual["player_1_points"] == decoded.board.player_1.points
    assert actual["player_0_bar"] == decoded.board.player_0.bar
    assert actual["player_1_bar"] == decoded.board.player_1.bar
    assert parsed.state.on_roll == {
        "player_0": "player_x",
        "player_1": "player_o",
    }[decoded.state.on_roll]
    assert parsed.state.cube_owner == {
        "player_0": "player_x",
        "player_1": "player_o",
        "center": "centered",
    }[decoded.cube.owner]
    assert (parsed.state.score_x, parsed.state.score_o) == (
        decoded.score.player_0,
        decoded.score.player_1,
    )


def test_supported_ankigammon_position_and_xgid_parse_encode_round_trips():
    position, metadata = parse_gnuid(POSITION_ID)
    assert _direct_gnu_encode(position, metadata, only_position=True) == POSITION_ID

    position, metadata = parse_xgid(XGID_CUBE)
    assert encode_xgid(
        position,
        cube_value=metadata["cube_value"],
        cube_owner=metadata["cube_owner"],
        dice=metadata.get("dice"),
        on_roll=metadata["on_roll"],
        score_x=metadata["score_x"],
        score_o=metadata["score_o"],
        match_length=metadata["match_length"],
        crawford_jacoby=metadata["crawford_jacoby"],
        max_cube=metadata["max_cube"],
    ) == XGID_CUBE


def test_bridge_gnuid_encoder_is_independent_of_ankigammon_label_bits():
    for raw_identifier, decision_type, expected_bits in (
        (CHECKER_GNUID, "checker", [6, 66]),
        (RET_003_PLAYING_CUBE_GNUID, "cube", [6, 66]),
    ):
        position, metadata = parse_gnuid(raw_identifier)
        raw_ankigammon_output = _direct_gnu_encode(position, metadata)
        assert raw_ankigammon_output != raw_identifier

        raw_match = base64.b64decode(raw_ankigammon_output.split(":", 1)[1] + "=")
        expected_match = base64.b64decode(raw_identifier.split(":", 1)[1] + "=")
        differing_bits = []
        for bit in range(72):
            if ((raw_match[bit // 8] >> (bit % 8)) & 1) != ((expected_match[bit // 8] >> (bit % 8)) & 1):
                differing_bits.append(bit)
        assert differing_bits == expected_bits

        corrected = to_sage_request(raw_identifier, decision_type)
        assert corrected.ready
        assert corrected.engine_identifier == raw_identifier


def test_ankigammon_complete_gnuid_semantic_parse_encode_round_trip():
    position, metadata = parse_gnuid(CHECKER_GNUID)
    encoded = _direct_gnu_encode(position, metadata)
    round_position, round_metadata = parse_gnuid(encoded)
    assert round_position.points == position.points
    for field in ("cube_value", "cube_owner", "dice", "on_roll", "score_x", "score_o", "match_length", "crawford"):
        assert round_metadata.get(field) == metadata.get(field)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "bad",
        "PAAAICMAAAAAAA:short",
        "XGID=bad",
        "XGID=--------------------------:0:0:0:00:0:0:0:0:10",
        CHECKER_GNUID + " ",
    ],
)
def test_malformed_or_unsupported_identifier_rejection(raw):
    assert detect_identifier_format(raw) == "invalid_or_unsupported"
    with pytest.raises(UnsupportedAnalysisIdentifier):
        parse_analysis_identifier(raw)


def test_gnu_native_request_prefers_original_complete_gnuid_and_provenance():
    prepared = to_gnu_request(CHECKER_GNUID, "checker")
    assert prepared.ready
    assert prepared.request.engine == "gnu"
    assert prepared.request.position.id == CHECKER_GNUID
    assert prepared.request.dice == (4, 2)
    assert prepared.identifier_provenance == "original_complete_gnuid"
    assert prepared.conversion_applied is False
    assert prepared.canonical_request.identifier.raw_identifier == CHECKER_GNUID


@pytest.mark.parametrize(
    "raw_identifier,decision_type",
    [
        (RET_002_CHECKER_GNUID, "checker"),
        (RET_003_PLAYING_CUBE_GNUID, "cube"),
        (RET_006_CHECKER_GNUID, "checker"),
        (RET_010_POST_CRAWFORD_CUBE_GNUID, "cube"),
    ],
)
def test_corrected_complete_gnuid_keeps_exact_raw_gnu_request_and_provenance(
    raw_identifier, decision_type
):
    prepared = to_gnu_request(raw_identifier, decision_type)
    assert prepared.ready
    assert prepared.engine_identifier == raw_identifier
    assert prepared.request.position.id == raw_identifier
    assert prepared.identifier_provenance == "original_complete_gnuid"
    assert prepared.conversion_applied is False
    assert prepared.canonical_request.identifier.raw_identifier == raw_identifier


@pytest.mark.parametrize(
    "raw_identifier,decision_type",
    [
        (RET_002_CHECKER_GNUID, "checker"),
        (RET_003_PLAYING_CUBE_GNUID, "cube"),
        (RET_006_CHECKER_GNUID, "checker"),
        (RET_010_POST_CRAWFORD_CUBE_GNUID, "cube"),
    ],
)
def test_sage_preparation_context_matches_corrected_canonical_to_bgsage(
    raw_identifier, decision_type
):
    prepared = to_sage_request(raw_identifier, decision_type)
    assert prepared.ready
    assert prepared.identifier_provenance == "canonical_reencoding_from_complete_gnuid"
    expected = canonical_to_bgsage(_analysis_position(raw_identifier))
    actual = canonical_position_context(_analysis_position(prepared.engine_identifier))
    assert actual == expected
    assert prepared.semantic_equivalence_claimed is False


def test_sage_request_is_canonical_reencoding_with_no_output_equivalence_claim():
    prepared = to_sage_request(XGID_BOTTOM, "cube")
    assert prepared.ready
    assert prepared.request.engine == "sage"
    assert prepared.request.position.id == GNU_BOTTOM_ON_ROLL
    assert prepared.request.dice is None
    assert prepared.identifier_provenance == "canonical_conversion_from_original_xgid"
    assert prepared.conversion_applied is True
    assert prepared.configuration_state == {
        "cube_enabled": True,
        "variation": "standard",
        "jacoby": False,
        "beavers": False,
    }
    assert prepared.semantic_equivalence_claimed is False


def test_xgid_parse_snapshot_is_unchanged_and_crawford_cube_is_blocked():
    parsed = parse_analysis_identifier(XGID_CUBE)
    assert parsed.identifier_format == "xgid"
    assert parsed.source_turn == "player_o"
    assert parsed.canonical_player_mapping == {
        "player_x": "AnkiGammon Player.X; top; positive checkers",
        "player_o": "AnkiGammon Player.O; bottom; negative checkers",
    }
    assert parsed.source_player_mapping == {"top/X": "player_x", "bottom/O": "player_o"}
    for prepare in (to_gnu_request, to_sage_request):
        prepared = prepare(XGID_CUBE, "cube")
        assert prepared.status == "unsupported"
        assert "cube_decision_illegal_during_crawford" in prepared.unsupported_state


def test_configuration_conflict_is_reported_not_inferred_away():
    jacoby_xgid = XGID_TOP.replace(":0:0:0:0:10", ":0:0:1:0:10")
    prepared = to_gnu_request(jacoby_xgid, "cube")
    assert prepared.status == "unsupported"
    assert "jacoby conflicts with verified gnu configuration" in prepared.unsupported_state
    assert prepared.request is None


@pytest.mark.parametrize("field", ["jacoby", "beavers"])
def test_money_rule_configuration_conflicts_from_explicit_state_fail_closed(field):
    explicit_state = {
        "on_roll": "X",
        "dice": None,
        "cube_value": 1,
        "cube_owner": "centered",
        "score_x": 0,
        "score_o": 0,
        "match_length": 0,
        field: True,
    }
    prepared = to_gnu_request(POSITION_ID, "cube", explicit_state=explicit_state)
    assert prepared.status == "unsupported"
    assert "{} conflicts with verified gnu configuration".format(field) in prepared.unsupported_state
    assert prepared.request is None
