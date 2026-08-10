"""Engine Kit-owned strict XGID and combined GNU-ID decoders."""

from __future__ import annotations

import base64
import re

from .models import (
    BackgammonView,
    Board,
    CheckerCount,
    ConversionLoss,
    CubeState,
    DecodedPosition,
    MatchScore,
    ParserIdentity,
    PendingAction,
    PlayerBoard,
    PositionSource,
    PositionState,
    RawSource,
    RuleState,
    UniversalPosition,
)
from .semantics import derive_state, other_player
from .validation import validate_origin_coverage, validate_universal_position


_XGID_PROFILE = "xgid-v1-15-checker"
_GNU_PROFILE = "gnubg-combined-id-v1-15-checker"
_BASE64_14 = re.compile(r"^[A-Za-z0-9+/]{14}$")
_BASE64_12 = re.compile(r"^[A-Za-z0-9+/]{12}$")


def _origin(status, source_path=None, note=None):
    data = {"status": status}
    if source_path is not None:
        data["source_path"] = source_path
    if note is not None:
        data["note"] = note
    return data


def _player_board(points, bar, checker_count=15):
    total = sum(points) + bar
    if total > checker_count:
        raise ValueError("represented checkers exceed the 15-checker source profile")
    return PlayerBoard(tuple(points), bar, checker_count - total)


def _base_origins(board_source, checker_note):
    origins = {
        "/board/coordinate_system": _origin(
            "derived", note="The named source profile fixes the self-relative coordinate system."
        )
    }
    for player in ("player_0", "player_1"):
        origins["/board/{}/points".format(player)] = _origin("represented_directly", board_source)
        origins["/board/{}/bar".format(player)] = _origin("represented_directly", board_source)
        origins["/board/checker_count/{}".format(player)] = _origin("derived", note=checker_note)
        origins["/board/{}/off".format(player)] = _origin(
            "derived",
            note="checker_count minus represented points and bar",
        )
    return origins


def _common_losses(format_name):
    if format_name == "xgid":
        return (
            ConversionLoss("/cube/enabled", "XGID has no explicit cube-enabled or NoCube field.", "warning"),
            ConversionLoss("/rules/automatic_doubles", "XGID does not represent automatic-double settings.", "warning"),
            ConversionLoss("/rules/raccoons", "XGID does not represent raccoon permission.", "warning"),
            ConversionLoss("/rules/variation", "XGID does not identify the variation.", "warning"),
        )
    return (
        ConversionLoss("/cube/enabled", "Combined GNU ID does not establish cubeful versus NoCube evaluation.", "warning"),
        ConversionLoss("/rules/automatic_doubles", "Combined GNU ID does not represent automatic-double settings.", "warning"),
        ConversionLoss("/rules/beavers", "Combined GNU ID does not represent beaver permission.", "warning"),
        ConversionLoss("/rules/jacoby", "Combined GNU ID does not represent Jacoby.", "warning"),
        ConversionLoss("/rules/maximum_cube", "Combined GNU ID does not represent maximum cube.", "warning"),
        ConversionLoss("/rules/raccoons", "Combined GNU ID does not represent raccoon permission.", "warning"),
        ConversionLoss("/rules/variation", "Combined GNU ID does not identify the variation.", "warning"),
        ConversionLoss("/view", "Combined GNU IDs do not establish an arbitrary desktop rotation.", "informational"),
    )


def decode_xgid(raw_identifier):
    if not isinstance(raw_identifier, str) or not raw_identifier.startswith("XGID="):
        raise ValueError("XGID must begin with XGID=")
    parts = raw_identifier[5:].split(":")
    if len(parts) != 10:
        raise ValueError("XGID must contain a board and exactly nine metadata fields")
    board_text, cube_exp_text, cube_owner_text, turn_text, action = parts[:5]
    score_bottom_text, score_top_text, rule_text, match_length_text, maximum_cube_exp_text = parts[5:]
    if len(board_text) != 26:
        raise ValueError("XGID board must contain exactly 26 characters")
    if any(char != "-" and not ("A" <= char <= "P") and not ("a" <= char <= "p") for char in board_text):
        raise ValueError("XGID board contains an unsupported checker character")

    def integer(text, label, minimum=None, maximum=None):
        if not re.fullmatch(r"-?\d+", text):
            raise ValueError("XGID {} is not an integer".format(label))
        value = int(text)
        if minimum is not None and value < minimum:
            raise ValueError("XGID {} is below its supported range".format(label))
        if maximum is not None and value > maximum:
            raise ValueError("XGID {} is above its supported range".format(label))
        return value

    cube_exp = integer(cube_exp_text, "cube exponent", 0, 15)
    cube_owner_code = integer(cube_owner_text, "cube owner", -1, 1)
    turn_code = integer(turn_text, "turn", -1, 1)
    if turn_code not in (-1, 1):
        raise ValueError("XGID unresolved opening turn is outside universal-position-v1")
    score_bottom = integer(score_bottom_text, "bottom score", 0)
    score_top = integer(score_top_text, "top score", 0)
    rule_code = integer(rule_text, "rule field", 0, 3)
    match_length = integer(match_length_text, "match length", 0)
    maximum_cube_exp = integer(maximum_cube_exp_text, "maximum cube exponent", 0, 15)

    p0_points = [0] * 24
    p1_points = [0] * 24
    p0_bar = 0
    p1_bar = 0
    for offset, char in enumerate(board_text):
        if char == "-":
            continue
        count = ord(char.lower()) - ord("a") + 1
        if offset == 0:
            if not char.islower():
                raise ValueError("XGID top bar must contain top-player checkers")
            p0_bar = count
        elif offset == 25:
            if not char.isupper():
                raise ValueError("XGID bottom bar must contain bottom-player checkers")
            p1_bar = count
        else:
            physical_point = offset
            if char.isupper():
                p1_points[physical_point - 1] = count
            else:
                p0_points[24 - physical_point] = count

    on_roll = "player_1" if turn_code == 1 else "player_0"
    cube_owner = {1: "player_1", 0: "center", -1: "player_0"}[cube_owner_code]
    cube_value = 2 ** cube_exp
    maximum_cube = 2 ** maximum_cube_exp
    dice = None
    pending = PendingAction.none()
    if action == "00":
        pass
    elif re.fullmatch(r"[1-6]{2}", action):
        dice = (int(action[0]), int(action[1]))
    elif action == "D":
        pending = PendingAction("double", on_roll, other_player(on_roll), cube_value * 2, None)
    elif action == "B":
        pending = PendingAction("beaver", other_player(on_roll), on_roll, cube_value * 2, None)
    elif action == "R":
        pending = PendingAction("raccoon", on_roll, other_player(on_roll), cube_value * 2, None)
    else:
        raise ValueError("XGID dice/action field is unsupported")

    if match_length == 0:
        crawford = False
        jacoby = bool(rule_code & 1)
        beavers = bool(rule_code & 2)
    else:
        if rule_code not in (0, 1):
            raise ValueError("match-play XGID Crawford field must be 0 or 1")
        crawford = bool(rule_code)
        jacoby = None
        beavers = None

    position = UniversalPosition(
        board=Board(
            CheckerCount(15, 15),
            _player_board(p0_points, p0_bar),
            _player_board(p1_points, p1_bar),
        ),
        state=PositionState("playing", on_roll, None, "unknown", "unknown", dice),
        cube=CubeState(None, cube_value, cube_owner, pending),
        score=MatchScore(score_top, score_bottom, match_length),
        rules=RuleState(None, crawford, jacoby, beavers, None, None, maximum_cube),
    )
    position = derive_state(position)
    validate_universal_position(position)

    origins = _base_origins(
        "XGID board",
        "The named xgid-v1-15-checker profile fixes checker_count at 15.",
    )
    origins.update(
        {
            "/state/game_state": _origin("derived", note="A valid post-opening XGID is an active position."),
            "/state/on_roll": _origin("represented_directly", "XGID turn"),
            "/state/decision_player": _origin("derived"),
            "/state/phase": _origin("derived"),
            "/state/decision_type": _origin("derived"),
            "/state/dice": _origin("represented_directly", "XGID dice/action"),
            "/cube/enabled": _origin("unknown", note="XGID has no explicit cube-enabled field."),
            "/cube/value": _origin("represented_directly", "XGID cube exponent"),
            "/cube/owner": _origin("represented_directly", "XGID cube position"),
            "/cube/pending_action": _origin("derived", "XGID dice/action"),
            "/score/player_0": _origin("represented_directly", "XGID top score"),
            "/score/player_1": _origin("represented_directly", "XGID bottom score"),
            "/score/match_length": _origin("represented_directly", "XGID match length"),
            "/rules/crawford": _origin("derived" if match_length == 0 else "represented_directly", "XGID rules"),
            "/rules/jacoby": _origin("represented_directly" if match_length == 0 else "not_represented", "XGID rules"),
            "/rules/beavers": _origin("represented_directly" if match_length == 0 else "not_represented", "XGID rules"),
            "/rules/raccoons": _origin("not_represented"),
            "/rules/automatic_doubles": _origin("not_represented"),
            "/rules/variation": _origin("not_represented"),
            "/rules/maximum_cube": _origin("represented_directly", "XGID maximum cube exponent"),
        }
    )
    source = PositionSource(
        format="xgid",
        profile=_XGID_PROFILE,
        raw_source=RawSource.text(raw_identifier),
        parser=ParserIdentity("bms-xgid-adapter-v1", "1.0.0", None),
        player_mapping={"top": "player_0", "bottom": "player_1"},
        source_view_available=True,
        field_origins=origins,
        conversion_losses=_common_losses("xgid"),
    )
    view = BackgammonView(
        top_player="player_0",
        bottom_player="player_1",
        point_labels_for="player_0",
        bottom_home_board_side="right",
        cube_display_side="left",
        rotation="source",
        view_origin="source",
    )
    validate_origin_coverage(position, source)
    return DecodedPosition(position, source, view)


def _decode_base64(value, pattern, expected_bytes, label):
    if pattern.fullmatch(value) is None:
        raise ValueError("{} has invalid Base64 spelling".format(label))
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(value + padding, validate=True)
    except Exception as exc:
        raise ValueError("{} is not valid Base64".format(label)) from exc
    if len(decoded) != expected_bytes:
        raise ValueError("{} has an invalid decoded length".format(label))
    return decoded


def _little_endian_bits(data):
    return tuple((byte >> bit) & 1 for byte in data for bit in range(8))


def _bit_value(bits, start, width):
    return sum(bits[start + offset] << offset for offset in range(width))


def _decode_position_id(position_id):
    data = _decode_base64(position_id, _BASE64_14, 10, "GNU Position ID")
    bits = _little_endian_bits(data)
    cursor = 0
    players = []
    for _ in range(2):
        points = []
        for _point in range(25):
            count = 0
            while cursor < len(bits) and bits[cursor] == 1:
                count += 1
                cursor += 1
            if cursor >= len(bits):
                raise ValueError("GNU Position ID terminates inside a unary point count")
            cursor += 1
            points.append(count)
        if sum(points) > 15:
            raise ValueError("GNU Position ID exceeds the 15-checker profile")
        players.append(points)
    if any(bits[cursor:]):
        raise ValueError("GNU Position ID contains nonzero padding bits")
    return players


def decode_gnuid(combined_id, runtime_version="GNU Backgammon 1.08.003"):
    if not isinstance(combined_id, str) or combined_id.count(":") != 1:
        raise ValueError("combined GNU ID requires one Position ID and one Match ID")
    position_id, match_id = combined_id.split(":", 1)
    position_blocks = _decode_position_id(position_id)
    match_data = _decode_base64(match_id, _BASE64_12, 9, "GNU Match ID")
    bits = _little_endian_bits(match_data)

    cube_exp = _bit_value(bits, 0, 4)
    cube_owner_code = _bit_value(bits, 4, 2)
    dice_owner_code = _bit_value(bits, 6, 1)
    crawford = bool(_bit_value(bits, 7, 1))
    game_state_code = _bit_value(bits, 8, 3)
    turn_owner_code = _bit_value(bits, 11, 1)
    doubled = bool(_bit_value(bits, 12, 1))
    resignation = _bit_value(bits, 13, 2)
    die1 = _bit_value(bits, 15, 3)
    die2 = _bit_value(bits, 18, 3)
    match_length = _bit_value(bits, 21, 15)
    score0 = _bit_value(bits, 36, 15)
    score1 = _bit_value(bits, 51, 15)
    if tuple(bits[66:]) != (1, 0, 0, 0, 0, 0):
        raise ValueError("GNU Match ID has noncanonical framing bits")
    if cube_owner_code == 2:
        raise ValueError("GNU Match ID contains reserved cube-owner value")
    if (die1 == 0) != (die2 == 0) or die1 > 6 or die2 > 6:
        raise ValueError("GNU Match ID contains invalid dice")
    if doubled and resignation:
        raise ValueError("GNU Match ID cannot contain both a double and resignation offer")

    on_roll = "player_{}".format(dice_owner_code)
    turn_owner = "player_{}".format(turn_owner_code)
    first_block, second_block = position_blocks
    if on_roll == "player_0":
        p1_encoded, p0_encoded = first_block, second_block
    else:
        p0_encoded, p1_encoded = first_block, second_block
    p0 = _player_board(p0_encoded[:24], p0_encoded[24])
    p1 = _player_board(p1_encoded[:24], p1_encoded[24])

    cube_value = 2 ** cube_exp
    cube_owner = {0: "player_0", 1: "player_1", 3: "center"}[cube_owner_code]
    pending = PendingAction.none()
    if doubled:
        pending = PendingAction("double", other_player(turn_owner), turn_owner, cube_value * 2, None)
    elif resignation:
        pending = PendingAction("resignation", other_player(turn_owner), turn_owner, None, resignation)

    game_state = {0: "setup", 1: "playing", 2: "game_over", 3: "resigned", 4: "game_over"}.get(
        game_state_code, "unknown"
    )
    dice = None if die1 == 0 else (die1, die2)
    position = UniversalPosition(
        board=Board(CheckerCount(15, 15), p0, p1),
        state=PositionState(game_state, on_roll, turn_owner, "unknown", "unknown", dice),
        cube=CubeState(None, cube_value, cube_owner, pending),
        score=MatchScore(score0, score1, match_length),
        rules=RuleState(None, crawford, None, None, None, None, None),
    )
    position = derive_state(position)
    validate_universal_position(position)

    origins = _base_origins(
        "GNU Position ID",
        "The named gnubg-combined-id-v1-15-checker profile fixes checker_count at 15.",
    )
    origins.update(
        {
            "/state/game_state": _origin("represented_directly", "GNU Match ID GameState"),
            "/state/on_roll": _origin("represented_directly", "GNU Match ID DiceOwner"),
            "/state/decision_player": _origin("represented_directly", "GNU Match ID TurnOwner"),
            "/state/phase": _origin("derived"),
            "/state/decision_type": _origin("derived"),
            "/state/dice": _origin("represented_directly", "GNU Match ID dice"),
            "/cube/enabled": _origin("unknown"),
            "/cube/value": _origin("represented_directly", "GNU Match ID cube exponent"),
            "/cube/owner": _origin("represented_directly", "GNU Match ID CubeOwner"),
            "/cube/pending_action/type": _origin("represented_directly", "GNU Match ID doubled/resignation bits"),
            "/cube/pending_action/offerer": _origin("derived", "GNU Match ID TurnOwner"),
            "/cube/pending_action/responder": _origin("represented_directly", "GNU Match ID TurnOwner"),
            "/cube/pending_action/offered_cube_value": _origin("derived", "GNU Match ID cube exponent"),
            "/cube/pending_action/resignation_multiplier": _origin("represented_directly", "GNU Match ID resignation bits"),
            "/score/player_0": _origin("represented_directly", "GNU Match ID player 0 score"),
            "/score/player_1": _origin("represented_directly", "GNU Match ID player 1 score"),
            "/score/match_length": _origin("represented_directly", "GNU Match ID match length"),
            "/rules/crawford": _origin("represented_directly", "GNU Match ID Crawford bit"),
            "/rules/jacoby": _origin("not_represented"),
            "/rules/beavers": _origin("not_represented"),
            "/rules/raccoons": _origin("not_represented"),
            "/rules/automatic_doubles": _origin("not_represented"),
            "/rules/variation": _origin("not_represented"),
            "/rules/maximum_cube": _origin("not_represented"),
        }
    )
    source = PositionSource(
        format="gnuid",
        profile=_GNU_PROFILE,
        raw_source=RawSource.text(combined_id),
        parser=ParserIdentity("bms-gnuid-adapter-v1", "1.0.0", runtime_version),
        player_mapping={"gnu_player_0": "player_0", "gnu_player_1": "player_1"},
        source_view_available=False,
        field_origins=origins,
        conversion_losses=_common_losses("gnuid"),
    )
    validate_origin_coverage(position, source)
    return DecodedPosition(position, source, None)
