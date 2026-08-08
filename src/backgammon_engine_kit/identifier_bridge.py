"""Public identifier-to-analysis-request bridge.

AnkiGammon supplies native metadata, but Engine Kit owns stable checker and
player identity across XGID and GNUID.  The bridge preserves source identity
and perspective facts while keeping checker placement separate from match
state.  It prepares, but never executes, the existing Engine Kit GNU and Sage
requests.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from ankigammon.models import CubeState as AnkiCubeState
from ankigammon.models import Player as AnkiPlayer
from ankigammon.models import Position as AnkiPosition
from ankigammon.utils.gnuid import parse_gnuid
from ankigammon.utils.xgid import parse_xgid

from .models import AnalysisRequest, Position


IDENTIFIER_FORMAT_COMPLETE_GNUID = "complete_gnuid"
IDENTIFIER_FORMAT_POSITION_ID = "position_id"
IDENTIFIER_FORMAT_XGID = "xgid"
IDENTIFIER_FORMAT_INVALID = "invalid_or_unsupported"

_POSITION_ID = re.compile(r"^[A-Za-z0-9+/]{14}$")
_COMPLETE_GNUID = re.compile(r"^[A-Za-z0-9+/]{14}:[A-Za-z0-9+/]{12}$")
_XGID = re.compile(
    r"^XGID=[A-Pa-p-]{26}:-?\d+:-?\d+:-?\d+:(?:00|[1-6]{2}|[DBR]):"
    r"\d+:\d+:\d+:\d+:\d+$"
)

_STATE_FIELDS = (
    "on_roll",
    "dice",
    "cube_value",
    "cube_owner",
    "score_x",
    "score_o",
    "match_length",
    "crawford",
    "jacoby",
    "beavers",
    "max_cube",
    "cube_enabled",
    "variation",
    "game_state",
)
_ENCODING_REQUIRED = (
    "on_roll",
    "dice",
    "cube_value",
    "cube_owner",
    "score_x",
    "score_o",
    "match_length",
    "crawford",
)
_CONFIGURATION_STATE = MappingProxyType(
    {
        "cube_enabled": True,
        "variation": "standard",
        "jacoby": False,
        "beavers": False,
    }
)
_SOURCE_PLAYER_MAPPING = MappingProxyType(
    {"top/X": "player_x", "bottom/O": "player_o"}
)
_CANONICAL_PLAYER_MAPPING = MappingProxyType(
    {
        "player_x": "AnkiGammon Player.X; top; positive checkers",
        "player_o": "AnkiGammon Player.O; bottom; negative checkers",
    }
)
_COMPLETE_GNUID_SOURCE_PLAYER_MAPPING = MappingProxyType(
    {
        "GNU player 0 / XGID top / X": "player_x",
        "GNU player 1 / XGID bottom / O": "player_o",
    }
)


class IdentifierBridgeError(ValueError):
    """Base error for invalid identifiers or contradictory explicit state."""


class UnsupportedAnalysisIdentifier(IdentifierBridgeError):
    """Raised when an identifier is malformed or outside the supported formats."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("unsupported public metadata value: {}".format(type(value).__name__))


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class CanonicalCheckerPosition:
    """Stable absolute board: X/top positive, O/bottom negative."""

    points: Tuple[int, ...]
    x_off: int
    o_off: int

    def __post_init__(self):
        object.__setattr__(self, "points", tuple(self.points))
        if len(self.points) != 26 or any(not isinstance(value, int) for value in self.points):
            raise ValueError("canonical checker position requires 26 integer points")
        if self.x_off < 0 or self.o_off < 0:
            raise ValueError("identifier represents more than 15 checkers for a player")
        for value in self.points:
            if value < -15 or value > 15:
                raise ValueError("identifier has an invalid checker count")

    def to_dict(self):
        return {"points": list(self.points), "x_off": self.x_off, "o_off": self.o_off}


@dataclass(frozen=True)
class CanonicalAnalysisState:
    """Identifier state with per-field availability kept distinct from null values."""

    on_roll: Optional[str]
    dice: Optional[Tuple[int, int]]
    cube_value: Optional[int]
    cube_owner: Optional[str]
    score_x: Optional[int]
    score_o: Optional[int]
    match_length: Optional[int]
    crawford: Optional[bool]
    jacoby: Optional[bool]
    beavers: Optional[bool]
    max_cube: Optional[int]
    cube_enabled: Optional[bool]
    variation: Optional[str]
    game_state: Optional[int]
    availability: Mapping[str, str]

    def __post_init__(self):
        if self.dice is not None:
            object.__setattr__(self, "dice", tuple(self.dice))
        availability = dict(self.availability)
        if set(availability) != set(_STATE_FIELDS):
            raise ValueError("availability must cover every canonical state field")
        allowed = {"available", "unavailable", "not_applicable", "unsupported"}
        if any(value not in allowed for value in availability.values()):
            raise ValueError("invalid state availability value")
        object.__setattr__(self, "availability", MappingProxyType(availability))

    @property
    def unavailable_fields(self):
        return tuple(sorted(name for name, value in self.availability.items() if value == "unavailable"))

    @property
    def unsupported_fields(self):
        return tuple(sorted(name for name, value in self.availability.items() if value == "unsupported"))

    def to_dict(self):
        values = {name: getattr(self, name) for name in _STATE_FIELDS}
        if values["dice"] is not None:
            values["dice"] = list(values["dice"])
        values["availability"] = dict(self.availability)
        return values


@dataclass(frozen=True)
class ParsedAnalysisIdentifier:
    """Loss-aware parse result retaining exact source and perspective provenance."""

    raw_identifier: str
    identifier_format: str
    native_metadata: Mapping[str, Any]
    canonical_position: CanonicalCheckerPosition
    state: CanonicalAnalysisState
    source_turn: Optional[str]
    source_orientation: str
    source_player_mapping: Mapping[str, str]
    canonical_player_mapping: Mapping[str, str]
    normalization_applied: bool
    point_reversal_applied: bool
    bar_reversal_applied: bool
    unavailable_state: Tuple[str, ...]
    unsupported_state: Tuple[str, ...]
    position_id: str
    match_id: Optional[str]

    def __post_init__(self):
        object.__setattr__(self, "native_metadata", _freeze(dict(self.native_metadata)))
        object.__setattr__(self, "source_player_mapping", _freeze(dict(self.source_player_mapping)))
        object.__setattr__(self, "canonical_player_mapping", _freeze(dict(self.canonical_player_mapping)))
        object.__setattr__(self, "unavailable_state", tuple(sorted(set(self.unavailable_state))))
        object.__setattr__(self, "unsupported_state", tuple(sorted(set(self.unsupported_state))))

    def to_dict(self):
        return {
            "raw_identifier": self.raw_identifier,
            "identifier_format": self.identifier_format,
            "native_metadata": _thaw(self.native_metadata),
            "canonical_position": self.canonical_position.to_dict(),
            "state": self.state.to_dict(),
            "source_turn": self.source_turn,
            "source_orientation": self.source_orientation,
            "source_player_mapping": _thaw(self.source_player_mapping),
            "canonical_player_mapping": _thaw(self.canonical_player_mapping),
            "normalization_applied": self.normalization_applied,
            "point_reversal_applied": self.point_reversal_applied,
            "bar_reversal_applied": self.bar_reversal_applied,
            "unavailable_state": list(self.unavailable_state),
            "unsupported_state": list(self.unsupported_state),
            "position_id": self.position_id,
            "match_id": self.match_id,
        }


@dataclass(frozen=True)
class CanonicalAnalysisRequest:
    """Engine-neutral request intent before an engine identifier is selected."""

    identifier: ParsedAnalysisIdentifier
    decision_type: str
    state: CanonicalAnalysisState
    state_provenance: Mapping[str, str]
    missing_state: Tuple[str, ...]
    unsupported_state: Tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "state_provenance", _freeze(dict(self.state_provenance)))
        object.__setattr__(self, "missing_state", tuple(sorted(set(self.missing_state))))
        object.__setattr__(self, "unsupported_state", tuple(sorted(set(self.unsupported_state))))

    def to_dict(self):
        return {
            "identifier": self.identifier.to_dict(),
            "decision_type": self.decision_type,
            "state": self.state.to_dict(),
            "state_provenance": _thaw(self.state_provenance),
            "missing_state": list(self.missing_state),
            "unsupported_state": list(self.unsupported_state),
        }


@dataclass(frozen=True)
class PreparedAnalysisRequest:
    """Ready, unavailable, or unsupported Engine Kit request preparation result."""

    engine: str
    status: str
    canonical_request: CanonicalAnalysisRequest
    request: Optional[AnalysisRequest]
    engine_identifier: Optional[str]
    identifier_provenance: str
    conversion_applied: bool
    configuration_state: Mapping[str, Any]
    missing_state: Tuple[str, ...]
    unsupported_state: Tuple[str, ...]
    semantic_equivalence_claimed: bool = False

    def __post_init__(self):
        if self.status not in ("ready", "unavailable", "unsupported"):
            raise ValueError("invalid prepared request status")
        if (self.status == "ready") != (self.request is not None):
            raise ValueError("only a ready preparation may contain an AnalysisRequest")
        object.__setattr__(self, "configuration_state", _freeze(dict(self.configuration_state)))
        object.__setattr__(self, "missing_state", tuple(sorted(set(self.missing_state))))
        object.__setattr__(self, "unsupported_state", tuple(sorted(set(self.unsupported_state))))

    @property
    def ready(self):
        return self.status == "ready"

    def to_dict(self):
        return {
            "engine": self.engine,
            "status": self.status,
            "canonical_request": self.canonical_request.to_dict(),
            "request": self.request.to_dict() if self.request is not None else None,
            "engine_identifier": self.engine_identifier,
            "identifier_provenance": self.identifier_provenance,
            "conversion_applied": self.conversion_applied,
            "configuration_state": _thaw(self.configuration_state),
            "missing_state": list(self.missing_state),
            "unsupported_state": list(self.unsupported_state),
            "semantic_equivalence_claimed": self.semantic_equivalence_claimed,
        }


def detect_identifier_format(raw_identifier: str) -> str:
    """Classify exact XGID, complete GNUID, Position ID, or invalid input.

    A colon is never treated as XGID evidence; XGID requires the exact prefix
    and complete ten-field shape.
    """

    if not isinstance(raw_identifier, str) or not raw_identifier:
        return IDENTIFIER_FORMAT_INVALID
    if _XGID.fullmatch(raw_identifier):
        fields = raw_identifier[5:].split(":")
        cube_exponent, cube_owner, turn = (int(value) for value in fields[1:4])
        score_o, score_x, rule, match_length, max_cube_exponent = (
            int(value) for value in fields[5:]
        )
        if (
            0 <= cube_exponent <= 15
            and cube_owner in (-1, 0, 1)
            and turn in (-1, 1)
            and score_o >= 0
            and score_x >= 0
            and rule in (0, 1, 2, 3)
            and match_length >= 0
            and 0 <= max_cube_exponent <= 15
        ):
            return IDENTIFIER_FORMAT_XGID
        return IDENTIFIER_FORMAT_INVALID
    if _COMPLETE_GNUID.fullmatch(raw_identifier):
        return IDENTIFIER_FORMAT_COMPLETE_GNUID
    if _POSITION_ID.fullmatch(raw_identifier):
        return IDENTIFIER_FORMAT_POSITION_ID
    return IDENTIFIER_FORMAT_INVALID


def _canonical_position(native: AnkiPosition) -> CanonicalCheckerPosition:
    return CanonicalCheckerPosition(tuple(native.points), native.x_off, native.o_off)


def _decode_base64_without_padding(value, expected_bytes, label):
    try:
        raw = base64.b64decode(value + "=" * ((4 - len(value) % 4) % 4), validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("{} is not valid Base64".format(label)) from exc
    if len(raw) != expected_bytes:
        raise ValueError("{} must decode to {} bytes".format(label, expected_bytes))
    return raw


def _little_endian_bits(raw):
    return tuple((byte >> bit) & 1 for byte in raw for bit in range(8))


def _bit_value(bits, start, count):
    value = 0
    for offset in range(count):
        value |= bits[start + offset] << offset
    return value


def _set_bits(bits, start, count, value):
    for offset in range(count):
        bits[start + offset] = (value >> offset) & 1


def _complete_gnuid_match_bits(raw_identifier):
    match_id = raw_identifier.split(":", 1)[1]
    return _little_endian_bits(_decode_base64_without_padding(match_id, 9, "GNU Match ID"))


def _complete_gnuid_on_roll(raw_identifier):
    """Return stable top/X or bottom/O identity from GNU DiceOwner.

    GNU player 0 is the XGID top/X player and GNU player 1 is the XGID
    bottom/O player.  AnkiGammon's ``Player.O``/``Player.X`` names for these
    bits are local implementation labels and must not escape into the
    canonical bridge.
    """

    dice_owner = _complete_gnuid_match_bits(raw_identifier)[6]
    return "player_x" if dice_owner == 0 else "player_o"


def _decode_gnu_position_blocks(position_id):
    """Decode the two relative 25-slot GNU Position ID checker blocks."""

    bits = _little_endian_bits(
        _decode_base64_without_padding(position_id, 10, "GNU Position ID")
    )
    blocks = []
    cursor = 0
    for _ in range(2):
        block = []
        for _ in range(25):
            count = 0
            while cursor < len(bits) and bits[cursor] == 1:
                count += 1
                cursor += 1
            if count > 15:
                raise ValueError("GNU Position ID exceeds the 15-checker profile")
            if cursor >= len(bits):
                raise ValueError("GNU Position ID terminates inside a unary point count")
            cursor += 1
            block.append(count)
        blocks.append(tuple(block))
    if any(bits[cursor:]):
        raise ValueError("GNU Position ID contains nonzero padding bits")
    return tuple(blocks)


def _canonical_position_from_complete_gnuid(raw_identifier):
    """Decode GNU's relative blocks into stable X/top and O/bottom checkers."""

    position_id = raw_identifier.split(":", 1)[0]
    first_block, second_block = _decode_gnu_position_blocks(position_id)
    on_roll = _complete_gnuid_on_roll(raw_identifier)
    if on_roll == "player_x":
        o_block, x_block = first_block, second_block
    else:
        x_block, o_block = first_block, second_block

    points = [0] * 26
    points[0] = x_block[24]
    points[25] = -o_block[24]
    for self_point, count in enumerate(x_block[:24], start=1):
        physical_point = 25 - self_point
        if count:
            points[physical_point] = count
    for self_point, count in enumerate(o_block[:24], start=1):
        physical_point = self_point
        if count:
            if points[physical_point]:
                raise ValueError("GNU Position ID places both players on one physical point")
            points[physical_point] = -count

    x_total = sum(x_block)
    o_total = sum(o_block)
    return CanonicalCheckerPosition(tuple(points), 15 - x_total, 15 - o_total)


def _xgid_checker_count(char):
    if char == "-":
        return 0
    if "a" <= char <= "p":
        return ord(char) - ord("a") + 1
    if "A" <= char <= "P":
        return -(ord(char) - ord("A") + 1)
    raise ValueError("XGID contains an invalid checker character")


def _canonical_position_from_xgid(raw_identifier):
    """Decode XGID checker ownership without AnkiGammon's turn-dependent swap.

    XGID case is an absolute player identity: lowercase is top/X and uppercase
    is bottom/O.  The turn field controls who acts, not checker ownership.
    """

    board_text = raw_identifier[5:].split(":", 1)[0]
    if len(board_text) != 26:
        raise ValueError("XGID board must contain 26 characters")
    points = [_xgid_checker_count(char) for char in board_text]
    if points[0] < 0:
        raise ValueError("XGID top bar cannot contain bottom/O checkers")
    if points[25] > 0:
        raise ValueError("XGID bottom bar cannot contain top/X checkers")
    x_total = sum(value for value in points if value > 0)
    o_total = sum(-value for value in points if value < 0)
    return CanonicalCheckerPosition(tuple(points), 15 - x_total, 15 - o_total)


def _empty_state():
    values = {name: None for name in _STATE_FIELDS}
    availability = {name: "unavailable" for name in _STATE_FIELDS}
    return values, availability


def _state(values, availability):
    return CanonicalAnalysisState(availability=availability, **values)


def _canonical_player(value):
    if value == AnkiPlayer.X:
        return "player_x"
    if value == AnkiPlayer.O:
        return "player_o"
    raise ValueError("AnkiGammon returned an unknown player")


def _canonical_cube_owner(value):
    return {
        AnkiCubeState.CENTERED: "centered",
        AnkiCubeState.X_OWNS: "player_x",
        AnkiCubeState.O_OWNS: "player_o",
    }[value]


def _parse_position_id(raw_identifier):
    native, native_metadata = parse_gnuid(raw_identifier)
    values, availability = _empty_state()
    state = _state(values, availability)
    return ParsedAnalysisIdentifier(
        raw_identifier=raw_identifier,
        identifier_format=IDENTIFIER_FORMAT_POSITION_ID,
        native_metadata=native_metadata,
        canonical_position=_canonical_position(native),
        state=state,
        source_turn=None,
        source_orientation="gnu-position-id-player-x-absolute",
        source_player_mapping=_SOURCE_PLAYER_MAPPING,
        canonical_player_mapping=_CANONICAL_PLAYER_MAPPING,
        normalization_applied=False,
        point_reversal_applied=False,
        bar_reversal_applied=False,
        unavailable_state=state.unavailable_fields,
        unsupported_state=(),
        position_id=raw_identifier,
        match_id=None,
    )


def _parse_complete_gnuid(raw_identifier):
    _, native_metadata = parse_gnuid(raw_identifier)
    match_bits = _complete_gnuid_match_bits(raw_identifier)
    on_roll = _complete_gnuid_on_roll(raw_identifier)
    ownership_normalized = on_roll == "player_x"
    cube_owner_code = _bit_value(match_bits, 4, 2)
    if cube_owner_code == 2:
        raise ValueError("GNU Match ID contains reserved cube-owner value")
    cube_owner = {0: "player_x", 1: "player_o", 3: "centered"}[cube_owner_code]
    values, availability = _empty_state()
    values.update(
        {
            "on_roll": on_roll,
            "dice": native_metadata.get("dice"),
            "cube_value": native_metadata["cube_value"],
            "cube_owner": cube_owner,
            "score_x": _bit_value(match_bits, 36, 15),
            "score_o": _bit_value(match_bits, 51, 15),
            "match_length": native_metadata["match_length"],
            "crawford": native_metadata["crawford"],
            "game_state": native_metadata["game_state"],
        }
    )
    for name in (
        "on_roll",
        "dice",
        "cube_value",
        "cube_owner",
        "score_x",
        "score_o",
        "match_length",
        "crawford",
        "game_state",
    ):
        availability[name] = "available"
    if values["match_length"]:
        availability["jacoby"] = "not_applicable"
        availability["beavers"] = "not_applicable"
    unsupported = []
    if native_metadata.get("doubled"):
        unsupported.append("pending_double")
    if native_metadata.get("resigned"):
        unsupported.append("pending_resignation")
    if values["game_state"] not in (0, 1):
        availability["game_state"] = "unsupported"
        unsupported.append("non_playing_game_state")
    state = _state(values, availability)
    position_id, match_id = raw_identifier.split(":", 1)
    return ParsedAnalysisIdentifier(
        raw_identifier=raw_identifier,
        identifier_format=IDENTIFIER_FORMAT_COMPLETE_GNUID,
        native_metadata=native_metadata,
        canonical_position=_canonical_position_from_complete_gnuid(raw_identifier),
        state=state,
        source_turn=values["on_roll"],
        source_orientation="gnu-position-id-relative-blocks-normalized-to-top-x-and-bottom-o",
        source_player_mapping=_COMPLETE_GNUID_SOURCE_PLAYER_MAPPING,
        canonical_player_mapping=_CANONICAL_PLAYER_MAPPING,
        normalization_applied=ownership_normalized,
        point_reversal_applied=False,
        bar_reversal_applied=False,
        unavailable_state=state.unavailable_fields,
        unsupported_state=tuple(unsupported) + state.unsupported_fields,
        position_id=position_id,
        match_id=match_id,
    )


def _parse_xgid(raw_identifier):
    _, native_metadata = parse_xgid(raw_identifier)
    values, availability = _empty_state()
    match_length = native_metadata["match_length"]
    values.update(
        {
            "on_roll": _canonical_player(native_metadata["on_roll"]),
            "dice": native_metadata.get("dice"),
            "cube_value": native_metadata["cube_value"],
            "cube_owner": _canonical_cube_owner(native_metadata["cube_owner"]),
            "score_x": native_metadata["score_x"],
            "score_o": native_metadata["score_o"],
            "match_length": match_length,
            "max_cube": native_metadata["max_cube"],
        }
    )
    for name in (
        "on_roll",
        "dice",
        "cube_value",
        "cube_owner",
        "score_x",
        "score_o",
        "match_length",
        "max_cube",
    ):
        availability[name] = "available"
    if match_length:
        values["crawford"] = bool(native_metadata["crawford_jacoby"] & 1)
        availability["crawford"] = "available"
        availability["jacoby"] = "not_applicable"
        availability["beavers"] = "not_applicable"
    else:
        availability["crawford"] = "not_applicable"
        values["jacoby"] = native_metadata["jacoby"]
        values["beavers"] = native_metadata["beavers_allowed"]
        availability["jacoby"] = "available"
        availability["beavers"] = "available"
    action = raw_identifier.split(":")[4]
    unsupported = () if action not in ("D", "B", "R") else ("pending_cube_action:" + action,)
    state = _state(values, availability)
    return ParsedAnalysisIdentifier(
        raw_identifier=raw_identifier,
        identifier_format=IDENTIFIER_FORMAT_XGID,
        native_metadata=native_metadata,
        canonical_position=_canonical_position_from_xgid(raw_identifier),
        state=state,
        source_turn=values["on_roll"],
        source_orientation="xgid-fixed-top-x-bottom-o",
        source_player_mapping=_SOURCE_PLAYER_MAPPING,
        canonical_player_mapping=_CANONICAL_PLAYER_MAPPING,
        normalization_applied=False,
        point_reversal_applied=False,
        bar_reversal_applied=False,
        unavailable_state=state.unavailable_fields,
        unsupported_state=unsupported,
        position_id=raw_identifier.split(":", 1)[0][5:],
        match_id=None,
    )


def parse_analysis_identifier(raw_identifier: str) -> ParsedAnalysisIdentifier:
    """Parse one exact supported identifier through AnkiGammon's public utilities."""

    identifier_format = detect_identifier_format(raw_identifier)
    try:
        if identifier_format == IDENTIFIER_FORMAT_COMPLETE_GNUID:
            return _parse_complete_gnuid(raw_identifier)
        if identifier_format == IDENTIFIER_FORMAT_POSITION_ID:
            return _parse_position_id(raw_identifier)
        if identifier_format == IDENTIFIER_FORMAT_XGID:
            return _parse_xgid(raw_identifier)
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedAnalysisIdentifier("AnkiGammon rejected the identifier: {}".format(exc)) from exc
    raise UnsupportedAnalysisIdentifier("identifier is malformed or unsupported")


def _normalize_explicit_state(name, value):
    if name == "on_roll":
        aliases = {"X": "player_x", "O": "player_o", "player_x": "player_x", "player_o": "player_o"}
        if value not in aliases:
            raise IdentifierBridgeError("on_roll must be X, O, player_x, or player_o")
        return aliases[value]
    if name == "cube_owner":
        aliases = {
            "X": "player_x",
            "O": "player_o",
            "player_x": "player_x",
            "player_o": "player_o",
            "center": "centered",
            "centered": "centered",
        }
        if value not in aliases:
            raise IdentifierBridgeError("cube_owner must identify X, O, or centered")
        return aliases[value]
    if name == "dice":
        if value is None:
            return None
        value = tuple(value)
        if len(value) != 2 or any(not isinstance(die, int) or die < 1 or die > 6 for die in value):
            raise IdentifierBridgeError("dice must be null or two integers from 1 through 6")
        return value
    if name in ("cube_value", "max_cube"):
        if not isinstance(value, int) or value < 1 or value & (value - 1):
            raise IdentifierBridgeError("{} must be a positive power of two".format(name))
    elif name in ("score_x", "score_o", "match_length", "game_state"):
        if not isinstance(value, int) or value < 0:
            raise IdentifierBridgeError("{} must be a non-negative integer".format(name))
    elif name in ("crawford", "jacoby", "beavers", "cube_enabled"):
        if not isinstance(value, bool):
            raise IdentifierBridgeError("{} must be boolean".format(name))
    elif name == "variation":
        if not isinstance(value, str) or not value:
            raise IdentifierBridgeError("variation must be a non-empty string")
    return value


def to_canonical_analysis_request(
    identifier,
    decision_type: str,
    *,
    explicit_state: Optional[Mapping[str, Any]] = None
) -> CanonicalAnalysisRequest:
    """Build engine-neutral request intent without defaulting unavailable ID state.

    ``explicit_state`` may fill fields absent from a Position ID.  It may not
    silently contradict a value represented by a complete identifier.
    """

    parsed = identifier if isinstance(identifier, ParsedAnalysisIdentifier) else parse_analysis_identifier(identifier)
    if decision_type not in ("checker", "cube"):
        raise IdentifierBridgeError("decision_type must be checker or cube")
    values = {name: getattr(parsed.state, name) for name in _STATE_FIELDS}
    availability = dict(parsed.state.availability)
    provenance = {
        name: "identifier" if availability[name] == "available" else availability[name]
        for name in _STATE_FIELDS
    }
    supplied = dict(explicit_state or {})
    unknown = set(supplied) - set(_STATE_FIELDS)
    if unknown:
        raise IdentifierBridgeError("unknown explicit state: {}".format(", ".join(sorted(unknown))))
    for name, raw_value in supplied.items():
        value = _normalize_explicit_state(name, raw_value)
        if availability[name] == "available" and values[name] != value:
            raise IdentifierBridgeError("explicit {} contradicts identifier state".format(name))
        values[name] = value
        availability[name] = "available"
        provenance[name] = "identifier+explicit" if provenance[name] == "identifier" else "explicit_state"

    match_length = values["match_length"]
    if availability["match_length"] == "available" and match_length == 0:
        if availability["crawford"] != "available":
            values["crawford"] = None
            availability["crawford"] = "not_applicable"
            provenance["crawford"] = "not_applicable"
    if availability["match_length"] == "available" and match_length and availability["crawford"] == "not_applicable":
        values["crawford"] = None
        availability["crawford"] = "unavailable"
        provenance["crawford"] = "unavailable"

    state = _state(values, availability)
    missing = list(state.unavailable_fields)
    unsupported = list(parsed.unsupported_state) + list(state.unsupported_fields)
    if decision_type == "checker" and availability["dice"] == "available" and values["dice"] is None:
        unsupported.append("checker_requires_dice")
    if decision_type == "cube" and availability["dice"] == "available" and values["dice"] is not None:
        unsupported.append("cube_requires_pre_roll_state_without_dice")
    if (
        decision_type == "cube"
        and availability["crawford"] == "available"
        and values["crawford"] is True
    ):
        unsupported.append("cube_decision_illegal_during_crawford")
    return CanonicalAnalysisRequest(
        identifier=parsed,
        decision_type=decision_type,
        state=state,
        state_provenance=provenance,
        missing_state=tuple(missing),
        unsupported_state=tuple(unsupported),
    )


def _canonical_player_blocks(position):
    """Return stable self-relative top/X and bottom/O GNU checker blocks."""

    points = position.points
    x_block = [max(points[25 - self_point], 0) for self_point in range(1, 25)]
    o_block = [max(-points[self_point], 0) for self_point in range(1, 25)]
    x_block.append(max(points[0], 0))
    o_block.append(max(-points[25], 0))
    if sum(x_block) + position.x_off != 15 or sum(o_block) + position.o_off != 15:
        raise IdentifierBridgeError("canonical checker totals do not equal 15 per player")
    return tuple(x_block), tuple(o_block)


def _encode_gnu_position_id(position, on_roll):
    """Encode stable players into GNU's DiceOwner-relative Position ID blocks."""

    x_block, o_block = _canonical_player_blocks(position)
    if on_roll == "player_x":
        blocks = (o_block, x_block)
    elif on_roll == "player_o":
        blocks = (x_block, o_block)
    else:
        raise IdentifierBridgeError("GNU encoding requires a known player on roll")

    bits = []
    for block in blocks:
        for count in block:
            bits.extend([1] * count)
            bits.append(0)
    if len(bits) > 80:
        raise IdentifierBridgeError("canonical checker position exceeds GNU Position ID capacity")
    bits.extend([0] * (80 - len(bits)))
    raw = bytearray(10)
    for index, bit in enumerate(bits):
        raw[index // 8] |= bit << (index % 8)
    return base64.b64encode(bytes(raw)).decode("ascii").rstrip("=")


def _encode_gnu_match_id(state):
    """Encode stable top/X and bottom/O state directly into GNU Match ID bits."""

    if state.game_state is not None and state.game_state not in (0, 1):
        raise IdentifierBridgeError("only setup or playing game state can be encoded for analysis")
    bits = [0] * 72
    cube_exponent = state.cube_value.bit_length() - 1
    _set_bits(bits, 0, 4, cube_exponent)
    cube_owner_code = {
        "player_x": 0,
        "player_o": 1,
        "centered": 3,
    }[state.cube_owner]
    _set_bits(bits, 4, 2, cube_owner_code)

    if state.on_roll == "player_x":
        on_roll_code = 0
    elif state.on_roll == "player_o":
        on_roll_code = 1
    else:
        raise IdentifierBridgeError("GNU encoding requires a known player on roll")
    bits[6] = on_roll_code
    bits[7] = 1 if state.crawford else 0
    _set_bits(bits, 8, 3, 1 if state.game_state is None else state.game_state)
    bits[11] = on_roll_code

    if state.dice is not None:
        _set_bits(bits, 15, 3, state.dice[0])
        _set_bits(bits, 18, 3, state.dice[1])
    _set_bits(bits, 21, 15, state.match_length)
    _set_bits(bits, 36, 15, state.score_x)
    _set_bits(bits, 51, 15, state.score_o)
    bits[66] = 1

    raw = bytearray(9)
    for index, bit in enumerate(bits):
        raw[index // 8] |= bit << (index % 8)
    return base64.b64encode(bytes(raw)).decode("ascii").rstrip("=")


def _encode_engine_gnuid(canonical_request):
    state = canonical_request.state
    position_id = _encode_gnu_position_id(
        canonical_request.identifier.canonical_position,
        state.on_roll,
    )
    return position_id + ":" + _encode_gnu_match_id(state)


def _prepare(identifier, decision_type, engine, configuration, prefer_original, explicit_state):
    canonical = to_canonical_analysis_request(
        identifier, decision_type, explicit_state=explicit_state
    )
    missing = []
    unsupported = list(canonical.unsupported_state)
    for name in _ENCODING_REQUIRED:
        availability = canonical.state.availability[name]
        if name == "crawford" and availability == "not_applicable":
            continue
        if availability == "unavailable":
            missing.append(name)
        elif availability == "unsupported":
            unsupported.append(name)
    for name, expected in _CONFIGURATION_STATE.items():
        availability = canonical.state.availability[name]
        if availability == "available" and getattr(canonical.state, name) != expected:
            unsupported.append("{} conflicts with verified {} configuration".format(name, engine))
    if unsupported:
        return PreparedAnalysisRequest(
            engine, "unsupported", canonical, None, None, "not_generated", False,
            _CONFIGURATION_STATE, tuple(missing), tuple(unsupported)
        )
    if missing:
        return PreparedAnalysisRequest(
            engine, "unavailable", canonical, None, None, "not_generated", False,
            _CONFIGURATION_STATE, tuple(missing), ()
        )

    parsed = canonical.identifier
    if prefer_original and parsed.identifier_format == IDENTIFIER_FORMAT_COMPLETE_GNUID:
        engine_identifier = parsed.raw_identifier
        provenance = "original_complete_gnuid"
        converted = False
    else:
        engine_identifier = _encode_engine_gnuid(canonical)
        converted = parsed.identifier_format != IDENTIFIER_FORMAT_COMPLETE_GNUID or engine_identifier != parsed.raw_identifier
        provenance = {
            IDENTIFIER_FORMAT_XGID: "canonical_conversion_from_original_xgid",
            IDENTIFIER_FORMAT_POSITION_ID: "canonical_conversion_from_position_id_plus_explicit_state",
            IDENTIFIER_FORMAT_COMPLETE_GNUID: "canonical_reencoding_from_complete_gnuid",
        }[parsed.identifier_format]
    request = AnalysisRequest(
        position=Position(id=engine_identifier, format="gnuid"),
        engine=engine,
        analysis_setting="1ply",
        decision_type=decision_type,
        dice=canonical.state.dice if decision_type == "checker" else None,
        configuration=configuration,
    )
    return PreparedAnalysisRequest(
        engine, "ready", canonical, request, engine_identifier, provenance, converted,
        _CONFIGURATION_STATE, (), ()
    )


def to_gnu_request(
    identifier,
    decision_type: str,
    *,
    explicit_state: Optional[Mapping[str, Any]] = None
) -> PreparedAnalysisRequest:
    """Prepare the verified GNU request, preferring an original complete GNUID."""

    from .gnu.config import verified_gnu_configuration

    return _prepare(
        identifier,
        decision_type,
        "gnu",
        verified_gnu_configuration(),
        True,
        explicit_state,
    )


def to_sage_request(
    identifier,
    decision_type: str,
    *,
    explicit_state: Optional[Mapping[str, Any]] = None
) -> PreparedAnalysisRequest:
    """Prepare Sage from canonical checker placement plus explicit available state.

    The resulting request is independently configured for Sage; this function
    makes no claim that future GNU and Sage analysis outputs are equivalent.
    """

    from .sage.config import verified_sage_configuration

    return _prepare(
        identifier,
        decision_type,
        "sage",
        verified_sage_configuration(),
        False,
        explicit_state,
    )


__all__ = (
    "CanonicalAnalysisRequest",
    "CanonicalAnalysisState",
    "CanonicalCheckerPosition",
    "IDENTIFIER_FORMAT_COMPLETE_GNUID",
    "IDENTIFIER_FORMAT_INVALID",
    "IDENTIFIER_FORMAT_POSITION_ID",
    "IDENTIFIER_FORMAT_XGID",
    "IdentifierBridgeError",
    "ParsedAnalysisIdentifier",
    "PreparedAnalysisRequest",
    "UnsupportedAnalysisIdentifier",
    "detect_identifier_format",
    "parse_analysis_identifier",
    "to_canonical_analysis_request",
    "to_gnu_request",
    "to_sage_request",
)
