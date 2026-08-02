"""Focused AnkiGammon identifier-bridge coverage.

Fixture provenance:
- CHECKER_GNUID and the original cube board come from the repository's
  committed GNU 1.08.003 evidence bundles.
- COMPLETE_CUBE_GNUID and both XGIDs were produced with AnkiGammon 1.7.0
  parse/encode round trips.  GNU Match IDs include the two GNU-required bits
  covered by ``test_ankigammon_gnuid_encoder_defect_is_isolated_and_corrected``.
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


CHECKER_GNUID = "4PPgASTgc/ABMA:cAnqAAAAAAAE"
COMPLETE_CUBE_GNUID = "bD3BAQyYd2cEAA:ggHgAEAAEAAE"
POSITION_ID = "PAAAICMAAAAAAA"
GNU_TOP = "PAAAICMAAAAAAA:cAkAAAAAAAAE"
GNU_BOTTOM = "PAAAICMAAAAAAA:MAEAAAAAAAAE"
XGID_TOP = "XGID=---D---------------a--b-a-:0:0:-1:00:0:0:0:0:10"
XGID_BOTTOM = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
XGID_CUBE = "XGID=---bB-DCC-B-cA---a-dabb---:2:1:1:00:4:2:1:7:10"


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
    assert parsed.state.cube_owner == "player_o"
    assert parsed.state.score_x == 2
    assert parsed.state.score_o == 4
    assert parsed.state.match_length == 7
    assert parsed.state.crawford is True


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


@pytest.mark.parametrize(
    "raw,on_roll,normalization,orientation",
    [
        (XGID_BOTTOM, "player_o", False, "xgid-bottom-on-roll-points-and-bars-forward"),
        (XGID_TOP, "player_x", True, "xgid-top-on-roll-points-and-bars-reversed"),
    ],
)
def test_xgid_exposes_bottom_and_top_perspective_effects(raw, on_roll, normalization, orientation):
    parsed = parse_analysis_identifier(raw)
    assert parsed.raw_identifier == raw
    assert parsed.source_turn == on_roll
    assert parsed.source_orientation == orientation
    assert parsed.normalization_applied is normalization
    assert parsed.point_reversal_applied is normalization
    assert parsed.bar_reversal_applied is normalization
    assert parsed.source_player_mapping == {"top/X": "player_x", "bottom/O": "player_o"}
    assert "positive checkers" in parsed.canonical_player_mapping["player_x"]


def test_matching_ankigammon_gnuid_and_xgid_pairs_have_equal_canonical_positions():
    assert parse_analysis_identifier(GNU_TOP).canonical_position == parse_analysis_identifier(XGID_TOP).canonical_position
    assert parse_analysis_identifier(GNU_BOTTOM).canonical_position == parse_analysis_identifier(XGID_BOTTOM).canonical_position
    assert parse_analysis_identifier(GNU_TOP).canonical_position == parse_analysis_identifier(GNU_BOTTOM).canonical_position


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


def test_ankigammon_gnuid_encoder_defect_is_isolated_and_corrected():
    for raw_identifier, decision_type, expected_bits in (
        (CHECKER_GNUID, "checker", [6, 66]),
        (COMPLETE_CUBE_GNUID, "cube", [66]),
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


def test_sage_request_is_canonical_reencoding_with_no_output_equivalence_claim():
    prepared = to_sage_request(XGID_CUBE, "cube")
    assert prepared.ready
    assert prepared.request.engine == "sage"
    assert prepared.request.position.id == COMPLETE_CUBE_GNUID
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


def test_configuration_conflict_is_reported_not_inferred_away():
    jacoby_xgid = XGID_TOP.replace(":0:0:0:0:10", ":0:0:1:0:10")
    prepared = to_gnu_request(jacoby_xgid, "cube")
    assert prepared.status == "unsupported"
    assert "jacoby conflicts with verified gnu configuration" in prepared.unsupported_state
    assert prepared.request is None
