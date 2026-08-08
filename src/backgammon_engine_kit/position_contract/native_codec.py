"""Native Python XGID and GNUID conversion over Universal Position v1.

This module is intentionally independent of AnkiGammon and R. It uses the
Engine Kit Universal Position contract as the canonical state and reproduces
GNU Backgammon 1.08.003 identifier behavior at the encoding boundary.

Canonical conversion preserves stable ``player_0`` and ``player_1`` identity.
Format-specific point ordering and GNU player-relative Position ID rows are
serialization details; they do not exchange player-dependent factual state.
GNU's optional interactive player swap is a post-import UI/state operation and
is deliberately outside identifier conversion.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Tuple

from .decoders import decode_xgid
from .models import (
    Board,
    CheckerCount,
    ConversionLoss,
    CubeState,
    MatchScore,
    PendingAction,
    PlayerBoard,
    PositionState,
    RuleState,
    UniversalPosition,
)
from .semantics import derive_state, other_player
from .validation import validate_universal_position


_BASE64_14 = re.compile(r"^[A-Za-z0-9+/]{14}$")
_BASE64_12 = re.compile(r"^[A-Za-z0-9+/]{12}$")
_DEFAULT_XGID_MAXIMUM_CUBE = 1024


class NativeIdentifierCodecError(ValueError):
    """Raised when an identifier or canonical state cannot be encoded safely."""


@dataclass(frozen=True)
class NormalizationChange:
    """One explicit normalization performed during a conversion."""

    field: str
    before: object
    after: object
    reason: str

    def to_dict(self):
        return {
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class NativeConversionResult:
    """Detailed conversion result for testing and future gallery evidence."""

    source_identifier: str
    source_format: str
    target_format: str
    identifier: str
    position: UniversalPosition
    normalizations: Tuple[NormalizationChange, ...] = ()
    losses: Tuple[ConversionLoss, ...] = ()

    def to_dict(self):
        return {
            "source_identifier": self.source_identifier,
            "source_format": self.source_format,
            "target_format": self.target_format,
            "identifier": self.identifier,
            "normalizations": [item.to_dict() for item in self.normalizations],
            "losses": [item.to_dict() for item in self.losses],
            "position": self.position.to_dict(),
        }


def _decode_base64(value, pattern, expected_bytes, label):
    if pattern.fullmatch(value) is None:
        raise NativeIdentifierCodecError("{} has invalid Base64 spelling".format(label))
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(value + padding, validate=True)
    except Exception as exc:
        raise NativeIdentifierCodecError("{} is not valid Base64".format(label)) from exc
    if len(decoded) != expected_bytes:
        raise NativeIdentifierCodecError("{} has an invalid decoded length".format(label))
    return decoded


def _little_endian_bits(data):
    return tuple((byte >> bit) & 1 for byte in data for bit in range(8))


def _bit_value(bits, start, width):
    return sum(bits[start + offset] << offset for offset in range(width))


def _set_bits(bits, start, width, value):
    for offset in range(width):
        bits[start + offset] = (value >> offset) & 1


def _bits_to_bytes(bits, byte_count):
    output = bytearray(byte_count)
    for index, value in enumerate(bits[: byte_count * 8]):
        if value:
            output[index // 8] |= 1 << (index % 8)
    return bytes(output)


def _base64_without_padding(data):
    return base64.b64encode(data).decode("ascii").rstrip("=")


def _power_of_two_exponent(value, label, maximum_exp=15):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise NativeIdentifierCodecError("{} must be a positive power of two".format(label))
    if value & (value - 1):
        raise NativeIdentifierCodecError("{} must be a positive power of two".format(label))
    exponent = value.bit_length() - 1
    if exponent > maximum_exp:
        raise NativeIdentifierCodecError("{} exceeds the encoded range".format(label))
    return exponent


def _player_board(encoded, checker_count=15):
    total = sum(encoded)
    if total > checker_count:
        raise NativeIdentifierCodecError("GNU Position ID exceeds the checker-count profile")
    return PlayerBoard(tuple(encoded[:24]), encoded[24], checker_count - total)


def _decode_position_id(position_id):
    data = _decode_base64(position_id, _BASE64_14, 10, "GNU Position ID")
    bits = _little_endian_bits(data)
    cursor = 0
    blocks = []
    for _player in range(2):
        encoded = []
        for _point in range(25):
            count = 0
            while cursor < len(bits) and bits[cursor] == 1:
                count += 1
                cursor += 1
            if cursor >= len(bits):
                raise NativeIdentifierCodecError(
                    "GNU Position ID terminates inside a unary point count"
                )
            cursor += 1
            encoded.append(count)
        if sum(encoded) > 15:
            raise NativeIdentifierCodecError("GNU Position ID exceeds the checker-count profile")
        blocks.append(encoded)
    if any(bits[cursor:]):
        raise NativeIdentifierCodecError("GNU Position ID contains nonzero padding bits")
    return blocks


def _position_block(player_board):
    return tuple(player_board.points) + (player_board.bar,)


def position_id_from_position(position):
    """Encode GNU's public 80-bit Position ID from a canonical position."""

    validate_universal_position(position)
    on_roll = position.state.on_roll
    if on_roll not in ("player_0", "player_1"):
        raise NativeIdentifierCodecError("GNU Position ID encoding requires a player on roll")

    # GNU keeps its internal board oriented to the side on roll. The public
    # Position ID unary blocks therefore change order with fMove.
    if on_roll == "player_0":
        blocks = (position.board.player_1, position.board.player_0)
    else:
        blocks = (position.board.player_0, position.board.player_1)

    bits = []
    for board in blocks:
        for count in _position_block(board):
            if count < 0:
                raise NativeIdentifierCodecError("checker counts cannot be negative")
            bits.extend([1] * count)
            bits.append(0)

    if len(bits) > 80:
        raise NativeIdentifierCodecError("checker state does not fit GNU's 80-bit Position ID")
    bits.extend([0] * (80 - len(bits)))
    return _base64_without_padding(_bits_to_bytes(bits, 10))


def _gnu_game_state_code(game_state):
    values = {
        "setup": 0,
        "playing": 1,
        "game_over": 2,
        "resigned": 3,
    }
    try:
        return values[game_state]
    except KeyError as exc:
        raise NativeIdentifierCodecError(
            "GNU Match ID cannot encode game_state={!r}".format(game_state)
        ) from exc


def _gnu_pending_fields(position, allow_lossy):
    pending = position.cube.pending_action
    if pending.type == "none":
        return False, 0, ()
    if pending.type == "unknown":
        if not allow_lossy:
            raise NativeIdentifierCodecError(
                "GNU Match ID encoding requires a known pending-action state"
            )
        return (
            False,
            0,
            (
                ConversionLoss(
                    "/cube/pending_action",
                    "GNU Match ID cannot safely encode an unknown pending-action state; it was omitted.",
                    "warning",
                ),
            ),
        )
    if pending.type == "double":
        return True, 0, ()
    if pending.type == "resignation":
        return False, int(pending.resignation_multiplier or 0), ()
    if pending.type in ("beaver", "raccoon"):
        if not allow_lossy:
            raise NativeIdentifierCodecError(
                "GNU Match ID cannot represent a pending {} action".format(pending.type)
            )
        return (
            False,
            0,
            (
                ConversionLoss(
                    "/cube/pending_action",
                    "GNU Match ID cannot represent a pending {} action; it was omitted.".format(
                        pending.type
                    ),
                    "warning",
                ),
            ),
        )
    raise NativeIdentifierCodecError(
        "GNU Match ID cannot encode pending action {!r}".format(pending.type)
    )


def match_id_from_position(position, allow_lossy=False):
    """Encode GNU Backgammon 1.08.003's 9-byte Match ID."""

    validate_universal_position(position)
    if position.cube.value is None:
        raise NativeIdentifierCodecError("GNU Match ID encoding requires cube.value")
    if position.cube.owner not in ("player_0", "player_1", "center"):
        raise NativeIdentifierCodecError("GNU Match ID encoding requires cube.owner")
    if position.state.on_roll not in ("player_0", "player_1"):
        raise NativeIdentifierCodecError("GNU Match ID encoding requires state.on_roll")

    cube_exp = _power_of_two_exponent(position.cube.value, "cube.value")
    cube_owner = {"player_0": 0, "player_1": 1, "center": 3}[position.cube.owner]
    roller = 0 if position.state.on_roll == "player_0" else 1
    doubled, resignation, _losses = _gnu_pending_fields(position, allow_lossy)

    decision_player = position.state.decision_player
    if decision_player not in ("player_0", "player_1"):
        # GNU Match ID always stores fTurn. For canonical states whose derived
        # decision player is intentionally null, keep the same player identity
        # as fMove. This is the normalization used by GNUID -> XGID -> GNUID
        # for setup metadata that XGID cannot preserve.
        decision_player = position.state.on_roll
    turn = 0 if decision_player == "player_0" else 1

    dice = position.state.dice
    if dice is None:
        die_high = die_low = 0
    else:
        if len(dice) != 2 or any(die < 1 or die > 6 for die in dice):
            raise NativeIdentifierCodecError("GNU Match ID dice must both be between 1 and 6")
        die_high, die_low = sorted((int(dice[0]), int(dice[1])), reverse=True)

    match_length = int(position.score.match_length)
    score0 = int(position.score.player_0)
    score1 = int(position.score.player_1)
    for label, value in (
        ("match length", match_length),
        ("player_0 score", score0),
        ("player_1 score", score1),
    ):
        if value < 0 or value >= 2 ** 15:
            raise NativeIdentifierCodecError("{} is outside GNU's 15-bit range".format(label))

    if match_length > 0:
        if position.rules.crawford is None:
            raise NativeIdentifierCodecError(
                "match-play GNU Match ID encoding requires rules.crawford"
            )
        crawford = bool(position.rules.crawford)
        # Jacoby does not apply to match play. GNU writes fJacobyInUse false.
        jacoby_in_use = False
    else:
        crawford = False
        if position.rules.jacoby is None:
            if not allow_lossy:
                raise NativeIdentifierCodecError(
                    "money-play GNU Match ID encoding requires rules.jacoby"
                )
            jacoby_in_use = False
        else:
            jacoby_in_use = bool(position.rules.jacoby)

    bits = [0] * 72
    for start, width, value in (
        (0, 4, cube_exp),
        (4, 2, cube_owner),
        (6, 1, roller),
        (7, 1, int(crawford)),
        (8, 3, _gnu_game_state_code(position.state.game_state)),
        (11, 1, turn),
        (12, 1, int(doubled)),
        (13, 2, resignation),
        (15, 3, die_high),
        (18, 3, die_low),
        (21, 15, match_length),
        (36, 15, score0),
        (51, 15, score1),
        # GNU stores the inverse of fJacobyInUse in bit 66.
        (66, 1, int(not jacoby_in_use)),
    ):
        _set_bits(bits, start, width, value)

    return _base64_without_padding(_bits_to_bytes(bits, 9))


def gnuid_from_position(position, allow_lossy=False):
    """Encode a complete ``PositionID:MatchID`` from canonical state."""

    return "{}:{}".format(
        position_id_from_position(position),
        match_id_from_position(position, allow_lossy=allow_lossy),
    )


def _decode_gnuid_state(combined_id):
    if not isinstance(combined_id, str) or combined_id.count(":") != 1:
        raise NativeIdentifierCodecError("combined GNU ID requires one Position ID and one Match ID")
    position_id, match_id = combined_id.split(":", 1)
    blocks = _decode_position_id(position_id)
    match_data = _decode_base64(match_id, _BASE64_12, 9, "GNU Match ID")
    bits = _little_endian_bits(match_data)

    cube_exp = _bit_value(bits, 0, 4)
    cube_owner_code = _bit_value(bits, 4, 2)
    roller_code = _bit_value(bits, 6, 1)
    crawford = bool(_bit_value(bits, 7, 1))
    game_state_code = _bit_value(bits, 8, 3)
    decision_code = _bit_value(bits, 11, 1)
    doubled = bool(_bit_value(bits, 12, 1))
    resignation = _bit_value(bits, 13, 2)
    die_high = _bit_value(bits, 15, 3)
    die_low = _bit_value(bits, 18, 3)
    match_length = _bit_value(bits, 21, 15)
    score0 = _bit_value(bits, 36, 15)
    score1 = _bit_value(bits, 51, 15)
    jacoby = not bool(_bit_value(bits, 66, 1))

    if any(bits[67:]):
        raise NativeIdentifierCodecError("GNU Match ID has nonzero reserved bits 67-71")
    if cube_owner_code == 2:
        raise NativeIdentifierCodecError("GNU Match ID contains reserved cube-owner value")
    if (die_high == 0) != (die_low == 0) or die_high > 6 or die_low > 6:
        raise NativeIdentifierCodecError("GNU Match ID contains invalid dice")
    if doubled and resignation:
        raise NativeIdentifierCodecError(
            "GNU Match ID cannot contain both a double and resignation offer"
        )

    on_roll = "player_{}".format(roller_code)
    raw_decision_player = "player_{}".format(decision_code)
    first, second = blocks
    if on_roll == "player_0":
        p1_encoded, p0_encoded = first, second
    else:
        p0_encoded, p1_encoded = first, second

    p0 = _player_board(p0_encoded)
    p1 = _player_board(p1_encoded)
    cube_owner = {0: "player_0", 1: "player_1", 3: "center"}[cube_owner_code]
    cube_value = 2 ** cube_exp

    pending = PendingAction.none()
    if doubled:
        pending = PendingAction(
            "double",
            other_player(raw_decision_player),
            raw_decision_player,
            cube_value * 2,
            None,
        )
    elif resignation:
        pending = PendingAction(
            "resignation",
            other_player(raw_decision_player),
            raw_decision_player,
            None,
            resignation,
        )

    game_state = {
        0: "setup",
        1: "playing",
        2: "game_over",
        3: "resigned",
        4: "game_over",
    }.get(game_state_code, "unknown")
    dice = None if die_high == 0 else (die_high, die_low)

    position = UniversalPosition(
        board=Board(CheckerCount(15, 15), p0, p1),
        state=PositionState(game_state, on_roll, raw_decision_player, "unknown", "unknown", dice),
        cube=CubeState(None, cube_value, cube_owner, pending),
        score=MatchScore(score0, score1, match_length),
        rules=RuleState(None, crawford, jacoby, None, None, None, None),
    )
    position = derive_state(position)
    validate_universal_position(position)
    return position, {
        "position_id": position_id,
        "match_id": match_id,
        "game_state_code": game_state_code,
        "raw_decision_player": raw_decision_player,
        "jacoby": jacoby,
    }


def position_from_gnuid(gnuid):
    """Decode a complete GNUID into the stable Universal Position model."""

    return _decode_gnuid_state(gnuid)[0]


def position_from_xgid(xgid):
    """Decode a complete XGID into the stable Universal Position model."""

    return decode_xgid(xgid).position


def _checker_character(count, uppercase):
    if count == 0:
        return "-"
    if count < 1 or count > 16:
        raise NativeIdentifierCodecError("XGID checker counts must be between 0 and 16")
    char = chr(ord("a") + count - 1)
    return char.upper() if uppercase else char


def _xgid_board_text(position):
    chars = ["-"] * 26
    chars[0] = _checker_character(position.board.player_0.bar, uppercase=False)
    chars[25] = _checker_character(position.board.player_1.bar, uppercase=True)
    for physical_point in range(1, 25):
        p1_count = position.board.player_1.points[physical_point - 1]
        p0_count = position.board.player_0.points[24 - physical_point]
        if p0_count and p1_count:
            raise NativeIdentifierCodecError(
                "both players occupy physical point {}".format(physical_point)
            )
        if p1_count:
            chars[physical_point] = _checker_character(p1_count, uppercase=True)
        elif p0_count:
            chars[physical_point] = _checker_character(p0_count, uppercase=False)
    return "".join(chars)


def _xgid_action(position, allow_lossy):
    pending = position.cube.pending_action
    if pending.type == "none":
        if position.state.dice is None:
            return "00", ()
        dice = position.state.dice
        if len(dice) != 2 or any(die < 1 or die > 6 for die in dice):
            raise NativeIdentifierCodecError("XGID dice must both be between 1 and 6")
        return "{}{}".format(int(dice[0]), int(dice[1])), ()
    if pending.type == "double":
        return "D", ()
    if pending.type == "beaver":
        return "B", ()
    if pending.type == "raccoon":
        return "R", ()
    if pending.type == "resignation":
        if not allow_lossy:
            raise NativeIdentifierCodecError("XGID cannot represent a pending resignation")
        return (
            "00",
            (
                ConversionLoss(
                    "/cube/pending_action",
                    "XGID cannot represent a pending resignation; it was omitted.",
                    "warning",
                ),
            ),
        )
    raise NativeIdentifierCodecError("XGID cannot encode pending action {!r}".format(pending.type))


def _xgid_rule_code(position, normalizations):
    if position.score.match_length > 0:
        if position.rules.crawford is None:
            raise NativeIdentifierCodecError("match-play XGID encoding requires rules.crawford")
        return int(bool(position.rules.crawford))

    jacoby = position.rules.jacoby
    if jacoby is None:
        jacoby = False
        normalizations.append(
            NormalizationChange(
                "/rules/jacoby",
                None,
                False,
                "XGID money-game output requires an explicit Jacoby value; defaulted off.",
            )
        )
    beavers = position.rules.beavers
    if beavers is None:
        beavers = False
        normalizations.append(
            NormalizationChange(
                "/rules/beavers",
                None,
                False,
                "GNU Match ID does not represent Beaver permission; XGID output defaults it off.",
            )
        )
    return int(bool(jacoby)) | (int(bool(beavers)) << 1)


def _xgid_from_position_detailed(position, allow_lossy=False, maximum_cube=None):
    validate_universal_position(position)
    if position.state.on_roll not in ("player_0", "player_1"):
        raise NativeIdentifierCodecError("XGID encoding requires a player on roll")
    if position.cube.value is None:
        raise NativeIdentifierCodecError("XGID encoding requires cube.value")
    if position.cube.owner not in ("player_0", "player_1", "center"):
        raise NativeIdentifierCodecError("XGID encoding requires cube.owner")

    normalizations = []
    losses = []
    if position.state.game_state == "setup":
        normalizations.append(
            NormalizationChange(
                "/state/game_state",
                "setup",
                "playing",
                "XGID has no GNU lifecycle state; setup metadata normalizes to an active factual position.",
            )
        )
    elif position.state.game_state != "playing":
        if not allow_lossy:
            raise NativeIdentifierCodecError(
                "XGID cannot represent game_state={!r}".format(position.state.game_state)
            )
        losses.append(
            ConversionLoss(
                "/state/game_state",
                "XGID cannot represent GNU lifecycle state {!r}; it was normalized to playing.".format(
                    position.state.game_state
                ),
                "warning",
            )
        )

    action, action_losses = _xgid_action(position, allow_lossy)
    losses.extend(action_losses)

    if maximum_cube is None:
        maximum_cube = position.rules.maximum_cube
    if maximum_cube is None:
        maximum_cube = _DEFAULT_XGID_MAXIMUM_CUBE
        normalizations.append(
            NormalizationChange(
                "/rules/maximum_cube",
                None,
                maximum_cube,
                "GNU Match ID does not represent XGID maximum cube; the conventional 1024 default was used.",
            )
        )
    max_exp = _power_of_two_exponent(maximum_cube, "maximum_cube")
    cube_exp = _power_of_two_exponent(position.cube.value, "cube.value")

    rule_code = _xgid_rule_code(position, normalizations)
    board_text = _xgid_board_text(position)
    cube_owner = {"player_0": -1, "center": 0, "player_1": 1}[position.cube.owner]
    turn = -1 if position.state.on_roll == "player_0" else 1
    score_bottom = position.score.player_1
    score_top = position.score.player_0

    xgid = "XGID={}:{}:{}:{}:{}:{}:{}:{}:{}:{}".format(
        board_text,
        cube_exp,
        cube_owner,
        turn,
        action,
        score_bottom,
        score_top,
        rule_code,
        position.score.match_length,
        max_exp,
    )
    return xgid, tuple(normalizations), tuple(losses)


def xgid_from_position(position, allow_lossy=False, maximum_cube=None):
    """Encode a complete XGID from canonical state."""

    return _xgid_from_position_detailed(
        position,
        allow_lossy=allow_lossy,
        maximum_cube=maximum_cube,
    )[0]


def _gnu_maximum_cube_loss(position):
    """Return the documented XGID maximum-cube loss, if any.

    GNU Match IDs do not serialize XGID's maximum-cube setting. Re-decoding a
    GNUID therefore normalizes that setting to max(1024, current cube value),
    matching the released backgammoncalculator 0.1.0 contract.
    """

    source_maximum = position.rules.maximum_cube
    if source_maximum is None:
        return None
    normalized_maximum = max(_DEFAULT_XGID_MAXIMUM_CUBE, int(position.cube.value or 1))
    if int(source_maximum) == normalized_maximum:
        return None
    return ConversionLoss(
        "/rules/maximum_cube",
        "GNU Match ID does not represent XGID maximum cube; {} would normalize to {} on GNUID -> XGID.".format(
            source_maximum, normalized_maximum
        ),
        "warning",
    )


def convert_xgid_to_gnuid(xgid, *, allow_lossy=False):
    """Convert XGID to canonical stable-player GNUID with evidence."""

    source_position = position_from_xgid(xgid)
    _doubled, _resignation, pending_losses = _gnu_pending_fields(
        source_position,
        allow_lossy,
    )
    losses = list(pending_losses)

    maximum_cube_loss = _gnu_maximum_cube_loss(source_position)
    if maximum_cube_loss is not None:
        if not allow_lossy:
            raise NativeIdentifierCodecError(
                "XGID contains a maximum-cube setting that GNUID cannot preserve; "
                "set allow_lossy=True to accept the documented normalization"
            )
        losses.append(maximum_cube_loss)

    if source_position.rules.beavers is True:
        if not allow_lossy:
            raise NativeIdentifierCodecError(
                "XGID enables Beaver rules that GNU Match ID cannot preserve; "
                "set allow_lossy=True to accept the documented loss"
            )
        losses.append(
            ConversionLoss(
                "/rules/beavers",
                "GNU Match ID does not represent Beaver permission; it was omitted.",
                "warning",
            )
        )

    identifier = gnuid_from_position(source_position, allow_lossy=allow_lossy)
    return NativeConversionResult(
        source_identifier=xgid,
        source_format="xgid",
        target_format="gnuid",
        identifier=identifier,
        position=source_position,
        normalizations=(),
        losses=tuple(losses),
    )


def xgid_to_gnuid(xgid, *, allow_lossy=False):
    """Convert XGID to the canonical stable-player complete GNUID."""

    return convert_xgid_to_gnuid(
        xgid,
        allow_lossy=allow_lossy,
    ).identifier


def convert_gnuid_to_xgid(gnuid, *, allow_lossy=False, maximum_cube=None):
    """Convert complete GNUID to XGID with explicit normalization evidence."""

    position, metadata = _decode_gnuid_state(gnuid)
    xgid, normalizations, losses = _xgid_from_position_detailed(
        position,
        allow_lossy=allow_lossy,
        maximum_cube=maximum_cube,
    )
    if metadata["game_state_code"] == 4:
        normalizations = tuple(normalizations) + (
            NormalizationChange(
                "/state/game_state_code",
                4,
                2,
                "Universal Position currently canonicalizes GNU game-over codes 2 and 4 to game_over.",
            ),
        )
    target_position = position_from_xgid(xgid)
    return NativeConversionResult(
        source_identifier=gnuid,
        source_format="gnuid",
        target_format="xgid",
        identifier=xgid,
        position=target_position,
        normalizations=tuple(normalizations),
        losses=tuple(losses),
    )


def gnuid_to_xgid(gnuid, *, allow_lossy=False, maximum_cube=None):
    """Convert a complete GNUID to a complete XGID."""

    return convert_gnuid_to_xgid(
        gnuid,
        allow_lossy=allow_lossy,
        maximum_cube=maximum_cube,
    ).identifier
