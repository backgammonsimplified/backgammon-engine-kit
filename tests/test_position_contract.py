import base64
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from backgammon_engine_kit.position_contract import (
    BGSageConversionError,
    ContractValidationError,
    EnrichmentError,
    GnuSourceBridgeError,
    PendingAction,
    UniversalPosition,
    canonical_to_bgsage,
    decode_gnuid,
    decode_xgid,
    derive_state,
    doubling_legality,
    enrich_position,
    semantic_state_hash,
    source_record_hash,
    validate_universal_position,
    verify_gnu_source_bridge,
    view_hash,
    with_source_hash,
)
from backgammon_engine_kit.position_contract.validation import validate_schema


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "contracts" / "universal-position-v1.2.2"
XGID_A = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
GNU_A = "PAAAICMAAAAAAA:cAkAAAAAAAAE"
XGID_B = "XGID=---D---------------a--b-a-:0:0:-1:00:0:0:0:0:8"
GNU_B = "PAAAICMAAAAAAA:MAEAAAAAAAAE"
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


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _enriched_pair(xgid=XGID_A, gnuid=GNU_A):
    xd = decode_xgid(xgid)
    gd = decode_gnuid(gnuid)
    xp, xs = enrich_position(xd.position, xd.source, X_CONTEXT)
    g_context = {
        "cube": {"enabled": True},
        "rules": {
            "variation": "standard",
            "jacoby": xd.position.rules.jacoby,
            "beavers": xd.position.rules.beavers,
            "automatic_doubles": 0,
            "maximum_cube": xd.position.rules.maximum_cube,
        },
    }
    gp, gs = enrich_position(gd.position, gd.source, g_context)
    return (xp, xs, xd.view), (gp, gs, gd.view)


def _semantic_fixture(name):
    return UniversalPosition.from_dict(_fixture("semantic-actions/" + name)["position"])


def _set_bits(bits, start, width, value):
    for offset in range(width):
        bits[start + offset] = (value >> offset) & 1


def _encode_match_id(
    *,
    cube_exp=0,
    cube_owner=3,
    dice_owner=0,
    crawford=0,
    game_state=1,
    turn_owner=0,
    doubled=0,
    resignation=0,
    die1=0,
    die2=0,
    match_length=0,
    score0=0,
    score1=0
):
    """Independent test-vector encoder from the published GNU bit layout."""
    bits = [0] * 72
    for start, width, value in (
        (0, 4, cube_exp),
        (4, 2, cube_owner),
        (6, 1, dice_owner),
        (7, 1, crawford),
        (8, 3, game_state),
        (11, 1, turn_owner),
        (12, 1, doubled),
        (13, 2, resignation),
        (15, 3, die1),
        (18, 3, die2),
        (21, 15, match_length),
        (36, 15, score0),
        (51, 15, score1),
    ):
        _set_bits(bits, start, width, value)
    # GNU's canonical 9-byte Match ID spelling has this framing bit set.
    bits[66] = 1
    raw = bytes(sum(bits[index + bit] << bit for bit in range(8)) for index in range(0, 72, 8))
    return base64.b64encode(raw).decode("ascii")


def _encode_position_id(player_0, player_1, on_roll):
    """Independent unary test-vector encoder for GNU Position IDs."""
    first = player_1 if on_roll == "player_0" else player_0
    second = player_0 if on_roll == "player_0" else player_1
    bits = []
    for block in (first, second):
        for count in list(block["points"]) + [block["bar"]]:
            bits.extend([1] * count)
            bits.append(0)
    bits.extend([0] * (80 - len(bits)))
    raw = bytes(sum(bits[index + bit] << bit for bit in range(8)) for index in range(0, 80, 8))
    return base64.b64encode(raw).decode("ascii").rstrip("=")


@pytest.mark.parametrize(
    "fixture_name,xgid,gnuid",
    [
        ("cross-format/position-a.cross-format-parity.json", XGID_A, GNU_A),
        ("cross-format/position-b.cross-format-parity.json", XGID_B, GNU_B),
    ],
)
def test_normative_raw_decodes_match_frozen_expected_objects(fixture_name, xgid, gnuid):
    fixture = _fixture(fixture_name)
    assert decode_xgid(xgid).position.to_dict() == fixture["expected_raw_decode"]["xgid"]
    assert decode_gnuid(gnuid).position.to_dict() == fixture["expected_raw_decode"]["gnuid"]


@pytest.mark.parametrize("xgid,gnuid", [(XGID_A, GNU_A), (XGID_B, GNU_B)])
def test_normative_enriched_parity_and_hash_separation(xgid, gnuid):
    (xp, xs, xv), (gp, gs, gv) = _enriched_pair(xgid, gnuid)
    assert xp == gp
    assert semantic_state_hash(xp) == semantic_state_hash(gp)
    assert source_record_hash(xs) != source_record_hash(gs)
    assert xv is not None
    assert gv is None


def test_position_a_and_b_keep_stable_checker_ownership_when_turn_changes():
    a = decode_xgid(XGID_A).position
    b = decode_xgid(XGID_B).position
    assert a.state.on_roll == "player_1"
    assert b.state.on_roll == "player_0"
    assert a.board.player_0 == b.board.player_1
    assert a.board.player_1 == b.board.player_0


def test_source_mappings_are_stable_and_not_colour_based():
    assert decode_xgid(XGID_A).source.player_mapping.to_dict() == {
        "bottom": "player_1",
        "top": "player_0",
    }
    assert decode_gnuid(GNU_A).source.player_mapping.to_dict() == {
        "gnu_player_0": "player_0",
        "gnu_player_1": "player_1",
    }


def test_raw_omissions_remain_null_and_origins_are_granular():
    x = decode_xgid(XGID_A)
    g = decode_gnuid(GNU_A)
    assert x.position.cube.enabled is None
    assert g.position.cube.enabled is None
    assert g.position.rules.jacoby is None
    assert g.position.rules.maximum_cube is None
    origins = x.source.field_origins.to_dict()
    assert origins["/board/checker_count/player_0"]["status"] == "derived"
    assert origins["/board/player_0/off"]["status"] == "derived"
    assert "/board" not in origins


def test_deep_immutability():
    decoded = decode_xgid(XGID_A)
    with pytest.raises(FrozenInstanceError):
        decoded.position.cube.enabled = True
    with pytest.raises(TypeError):
        decoded.source.field_origins["/cube/enabled"] = {"status": "supplied_externally"}
    with pytest.raises(TypeError):
        decoded.source.field_origins._dict["/cube/enabled"] = {"status": "unknown"}
    assert isinstance(decoded.position.board.player_0.points, tuple)


def test_enrichment_is_pure_and_records_each_supplied_leaf():
    raw = decode_gnuid(GNU_A)
    original_position = raw.position.to_dict()
    original_source = raw.source.to_dict()
    enriched, source = enrich_position(raw.position, raw.source, GNU_CONTEXT)
    assert raw.position.to_dict() == original_position
    assert raw.source.to_dict() == original_source
    assert enriched.state.decision_type == "roll_or_double"
    assert source.external_settings.to_dict() == GNU_CONTEXT
    origins = source.field_origins.to_dict()
    for path in (
        "/cube/enabled",
        "/rules/variation",
        "/rules/jacoby",
        "/rules/beavers",
        "/rules/automatic_doubles",
        "/rules/maximum_cube",
    ):
        assert origins[path]["status"] == "supplied_externally"


@pytest.mark.parametrize(
    "settings",
    [
        {"cube": {"value": 2}},
        {"state": {"decision_type": "roll_or_double"}},
        {"cube": {"pending_action": {"type": "double"}}},
        {"unknown": {"field": True}},
    ],
)
def test_enrichment_rejects_overwrite_derived_or_unknown_paths(settings):
    raw = decode_gnuid(GNU_A)
    with pytest.raises(EnrichmentError):
        enrich_position(raw.position, raw.source, settings)


def test_pre_roll_doubling_legality_legal_illegal_unknown():
    position, _, _ = _enriched_pair()[0]
    assert doubling_legality(position) == "legal"
    disabled = derive_state(replace(position, cube=replace(position.cube, enabled=False)))
    assert doubling_legality(disabled) == "illegal"
    unknown = decode_xgid(XGID_A).position
    assert doubling_legality(unknown) == "unknown"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "pending-double.semantic.json",
        "pending-beaver.semantic.json",
        "pending-raccoon.semantic.json",
    ],
)
def test_semantic_action_fixtures_are_valid(fixture_name):
    validate_universal_position(_semantic_fixture(fixture_name))


def test_known_illegal_pending_actions_are_rejected():
    double = _semantic_fixture("pending-double.semantic.json")
    with pytest.raises(ContractValidationError, match="Crawford"):
        validate_universal_position(replace(double, rules=replace(double.rules, crawford=True)))
    with pytest.raises(ContractValidationError, match="disabled"):
        validate_universal_position(replace(double, cube=replace(double.cube, enabled=False)))
    with pytest.raises(ContractValidationError, match="maximum"):
        validate_universal_position(replace(double, rules=replace(double.rules, maximum_cube=1)))
    inaccessible_pending = replace(double.cube.pending_action, offered_cube_value=4)
    inaccessible_cube = replace(
        double.cube, value=2, owner="player_1", pending_action=inaccessible_pending
    )
    with pytest.raises(ContractValidationError, match="cube access"):
        validate_universal_position(replace(double, cube=inaccessible_cube))

    beaver = _semantic_fixture("pending-beaver.semantic.json")
    with pytest.raises(ContractValidationError, match="maximum"):
        validate_universal_position(replace(beaver, rules=replace(beaver.rules, maximum_cube=2)))

    raccoon = _semantic_fixture("pending-raccoon.semantic.json")
    with pytest.raises(ContractValidationError, match="prohibited"):
        validate_universal_position(replace(raccoon, rules=replace(raccoon.rules, raccoons=False)))


def test_other_cross_field_contradictions_are_rejected():
    double = _semantic_fixture("pending-double.semantic.json")
    same = replace(
        double.cube.pending_action,
        responder=double.cube.pending_action.offerer,
    )
    with pytest.raises(ContractValidationError, match="distinct"):
        validate_universal_position(replace(double, cube=replace(double.cube, pending_action=same)))

    position, _, _ = _enriched_pair()[0]
    wrong_state = replace(position.state, phase="checker_play", decision_type="checker_play")
    with pytest.raises(ContractValidationError, match="state derivation mismatch"):
        validate_universal_position(replace(position, state=wrong_state))

    match = replace(
        position,
        score=replace(position.score, player_0=7, match_length=7),
        rules=replace(position.rules, jacoby=False, beavers=False),
    )
    match = derive_state(match)
    with pytest.raises(ContractValidationError, match="at or above match length"):
        validate_universal_position(match)


def test_schema_rejects_boolean_integer_unknown_field_and_appearance():
    position = decode_xgid(XGID_A).position.to_dict()
    position["board"]["checker_count"]["player_0"] = True
    with pytest.raises(ContractValidationError):
        validate_schema(position, "universal-position-v1")
    position = decode_xgid(XGID_A).position.to_dict()
    position["appearance"] = {"checker_color": "red"}
    with pytest.raises(ContractValidationError):
        validate_schema(position, "universal-position-v1")


def test_source_spelling_changes_source_hash_not_semantic_hash():
    first = decode_xgid(XGID_A)
    second = decode_xgid(XGID_A.replace(":0:0:0:0:10", ":00:00:0:0:10"))
    assert first.position == second.position
    assert semantic_state_hash(first.position) == semantic_state_hash(second.position)
    assert source_record_hash(first.source) != source_record_hash(second.source)


def test_view_changes_do_not_change_semantic_hash():
    decoded = decode_xgid(XGID_A)
    rotated = replace(
        decoded.view,
        top_player="player_1",
        bottom_player="player_0",
        point_labels_for="player_1",
        rotation="rotated",
    )
    assert view_hash(decoded.view) != view_hash(rotated)
    before = semantic_state_hash(decoded.position)
    assert semantic_state_hash(decoded.position) == before


def test_xgid_bar_dice_and_profile_derived_off_vector():
    board = "a" + ("-" * 24) + "B"
    decoded = decode_xgid("XGID={}:0:0:1:31:0:0:0:0:10".format(board))
    assert decoded.position.board.player_0.bar == 1
    assert decoded.position.board.player_1.bar == 2
    assert decoded.position.board.player_0.off == 14
    assert decoded.position.board.player_1.off == 13
    assert decoded.position.state.dice == (3, 1)
    assert decoded.position.state.decision_player == "player_1"
    assert decoded.position.state.phase == "checker_play"


def test_hand_built_gnu_vector_decodes_bar_dice_cube_score_and_turn_owners():
    p0 = {"points": [0] * 24, "bar": 1}
    p1 = {"points": [0] * 24, "bar": 2}
    pid = _encode_position_id(p0, p1, "player_1")
    mid = _encode_match_id(
        cube_exp=2,
        cube_owner=0,
        dice_owner=1,
        turn_owner=1,
        die1=6,
        die2=4,
        match_length=9,
        score0=3,
        score1=5,
    )
    decoded = decode_gnuid(pid + ":" + mid)
    position = decoded.position
    assert position.board.player_0.bar == 1
    assert position.board.player_1.bar == 2
    assert position.state.on_roll == "player_1"
    assert position.state.decision_player == "player_1"
    assert position.state.dice == (6, 4)
    assert position.cube.value == 4
    assert position.cube.owner == "player_0"
    assert position.score.to_dict() == {"player_0": 3, "player_1": 5, "match_length": 9}


def test_gnu_dice_owner_and_turn_owner_are_independent_for_pending_double():
    p0 = {"points": [0] * 24, "bar": 0}
    p1 = {"points": [0] * 24, "bar": 0}
    pid = _encode_position_id(p0, p1, "player_0")
    mid = _encode_match_id(dice_owner=0, turn_owner=1, doubled=1, cube_owner=3)
    decoded = decode_gnuid(pid + ":" + mid)
    assert decoded.position.state.on_roll == "player_0"
    assert decoded.position.state.decision_player == "player_1"
    assert decoded.position.cube.pending_action.offerer == "player_0"
    assert decoded.position.cube.pending_action.responder == "player_1"


@pytest.mark.parametrize(
    "bad",
    [
        "XGID=bad",
        "XGID=" + ("-" * 26) + ":0:0:0:00:0:0:0:0:10",
        "XGID=" + ("-" * 25) + "Q:0:0:1:00:0:0:0:0:10",
    ],
)
def test_malformed_or_unsupported_xgid_rejected(bad):
    with pytest.raises(ValueError):
        decode_xgid(bad)


@pytest.mark.parametrize("bad", ["bad", "PAAAICMAAAAAAA:bad", "PAAAICMAAAAAA!:cAkAAAAAAAAE"])
def test_malformed_gnuid_rejected(bad):
    with pytest.raises(ValueError):
        decode_gnuid(bad)


def test_gnu_source_bridge_accepts_match_and_rejects_mismatch_and_non_gnu():
    (_, _, _), (position, source, _) = _enriched_pair()
    source = with_source_hash(source)
    assert verify_gnu_source_bridge(position, source) == GNU_A
    changed = derive_state(replace(position, cube=replace(position.cube, enabled=False)))
    with pytest.raises(GnuSourceBridgeError, match="semantic hash"):
        verify_gnu_source_bridge(changed, source)
    x_position, x_source, _ = _enriched_pair()[0]
    with pytest.raises(GnuSourceBridgeError, match="format=gnuid"):
        verify_gnu_source_bridge(x_position, x_source)


def test_gnu_source_bridge_rejects_tampered_source_hash_and_invocation_context():
    (_, _, _), (position, source, _) = _enriched_pair()
    source = with_source_hash(source)
    tampered = replace(source, profile="wrong-profile")
    with pytest.raises(GnuSourceBridgeError):
        verify_gnu_source_bridge(position, tampered)
    with pytest.raises(GnuSourceBridgeError, match="invocation setting mismatch"):
        verify_gnu_source_bridge(position, source, invocation_settings={"/rules/jacoby": True})


def test_bgsage_conversion_both_on_roll_orientations_and_golden_board():
    a = _enriched_pair(XGID_A, GNU_A)[0][0]
    b = _enriched_pair(XGID_B, GNU_B)[0][0]
    converted_a = canonical_to_bgsage(a)
    converted_b = canonical_to_bgsage(b)
    assert converted_a.on_roll == "player_1"
    assert converted_b.on_roll == "player_0"
    assert converted_a.board == converted_b.board
    assert converted_a.board[1:7] == (1, 0, 2, 0, 0, 1)
    assert converted_a.board[22] == -4
    assert converted_a.player_off == converted_b.player_off == 11
    assert converted_a.opponent_off == converted_b.opponent_off == 11


def test_bgsage_bar_score_cube_and_dice_mapping():
    raw = decode_xgid("XGID=" + "a" + ("-" * 24) + "B:1:-1:-1:42:2:4:0:7:4")
    context = {
        "cube": {"enabled": True},
        "rules": {
            "variation": "standard",
            "automatic_doubles": 0,
            "raccoons": False,
            "jacoby": False,
            "beavers": False,
        },
    }
    position, _ = enrich_position(raw.position, raw.source, context)
    converted = canonical_to_bgsage(position)
    assert converted.on_roll == "player_0"
    assert converted.board[25] == 1
    assert converted.board[0] == -2
    assert converted.player_score == 4
    assert converted.opponent_score == 2
    assert converted.player_away == 3
    assert converted.opponent_away == 5
    assert converted.cube_owner == "player"
    assert converted.dice == (4, 2)


def test_bgsage_rejects_unresolved_required_context():
    with pytest.raises(BGSageConversionError, match="/cube/enabled"):
        canonical_to_bgsage(decode_xgid(XGID_A).position)


def test_live_adapter_boundary_seams_use_the_authoritative_guards():
    from backgammon_engine_kit.gnu.invocation import verified_source_id
    from backgammon_engine_kit.sage.invocation import canonical_position_context

    (_, _, _), (position, source, _) = _enriched_pair()
    source = with_source_hash(source)
    assert verified_source_id(position, source) == GNU_A
    assert canonical_position_context(position) == canonical_to_bgsage(position)


def test_bgsage_known_disabled_cube_is_preserved_not_defaulted():
    position, _, _ = _enriched_pair()[0]
    disabled = derive_state(replace(position, cube=replace(position.cube, enabled=False)))
    converted = canonical_to_bgsage(disabled)
    assert converted.cube_enabled is False
    assert converted.cube_value == 1
    assert converted.cube_owner == "centered"


def test_gnu_match_id_rejects_noncanonical_framing_bits():
    raw = bytearray(base64.b64decode(GNU_A.split(":", 1)[1]))
    raw[-1] = 0
    bad_mid = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(ValueError, match="framing"):
        decode_gnuid(GNU_A.split(":", 1)[0] + ":" + bad_mid)


@pytest.mark.parametrize(
    "fixture_name,xgid,gnuid",
    [
        ("cross-format/position-a.cross-format-parity.json", XGID_A, GNU_A),
        ("cross-format/position-b.cross-format-parity.json", XGID_B, GNU_B),
    ],
)
def test_normative_enrichment_matches_frozen_expected_position(fixture_name, xgid, gnuid):
    fixture = _fixture(fixture_name)
    (xp, _, _), (gp, _, _) = _enriched_pair(xgid, gnuid)
    assert xp.to_dict() == fixture["expected_enriched_position"]
    assert gp.to_dict() == fixture["expected_enriched_position"]


@pytest.mark.parametrize(
    "action,cube_exp,cube_owner,turn,expected_type,offerer,responder,offered",
    [
        ("D", 0, 0, 1, "double", "player_1", "player_0", 2),
        ("B", 1, 1, -1, "beaver", "player_1", "player_0", 4),
        ("R", 2, 1, -1, "raccoon", "player_0", "player_1", 8),
    ],
)
def test_xgid_pending_action_markers_decode_distinctly(
    action, cube_exp, cube_owner, turn, expected_type, offerer, responder, offered
):
    xgid = "XGID={}:{}:{}:{}:{}:0:0:3:0:3".format(
        "-" * 26, cube_exp, cube_owner, turn, action
    )
    decoded = decode_xgid(xgid).position
    pending = decoded.cube.pending_action
    assert pending.type == expected_type
    assert pending.offerer == offerer
    assert pending.responder == responder
    assert pending.offered_cube_value == offered


def test_gnu_resignation_and_game_state_decode_from_hand_built_vector():
    empty = {"points": [0] * 24, "bar": 0}
    pid = _encode_position_id(empty, empty, "player_0")
    mid = _encode_match_id(
        dice_owner=0,
        turn_owner=1,
        resignation=2,
        game_state=1,
    )
    position = decode_gnuid(pid + ":" + mid).position
    assert position.cube.pending_action.type == "resignation"
    assert position.cube.pending_action.offerer == "player_0"
    assert position.cube.pending_action.responder == "player_1"
    assert position.cube.pending_action.resignation_multiplier == 2
    assert position.state.phase == "resignation_response"
    assert position.state.decision_player == "player_1"


def test_source_array_order_does_not_change_source_hash():
    decoded = decode_xgid(XGID_A)
    first = replace(
        decoded.source,
        assumptions=("z", "a"),
        warnings=("second", "first"),
        conversion_losses=tuple(reversed(decoded.source.conversion_losses)),
    )
    second = replace(
        decoded.source,
        assumptions=("a", "z"),
        warnings=("first", "second"),
        conversion_losses=decoded.source.conversion_losses,
    )
    assert source_record_hash(first) == source_record_hash(second)


def test_appearance_is_rejected_from_source_and_view_contracts_too():
    source = decode_xgid(XGID_A).source.to_dict()
    source["appearance"] = {"font": "Example"}
    with pytest.raises(ContractValidationError):
        validate_schema(source, "position-source-v1")
    view = decode_xgid(XGID_A).view.to_dict()
    view["palette"] = "navy"
    with pytest.raises(ContractValidationError):
        validate_schema(view, "backgammon-view-v1")
