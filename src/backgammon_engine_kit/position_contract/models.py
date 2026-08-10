"""Deeply immutable models for the Universal Position contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Tuple
from types import MappingProxyType


UNIVERSAL_POSITION_VERSION = "universal-position-v1"
POSITION_SOURCE_VERSION = "position-source-v1"
BACKGAMMON_VIEW_VERSION = "backgammon-view-v1"
PLAYERS = ("player_0", "player_1")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict((str(key), _freeze_json(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("unsupported immutable JSON value: {}".format(type(value).__name__))


def _thaw_json(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class FrozenDict(Mapping):
    """Small deterministic immutable mapping for nested contract data."""

    __slots__ = ("_items", "_dict")

    def __init__(self, items=()):
        normalized = []
        seen = set()
        source = items.items() if isinstance(items, Mapping) else items
        for key, value in source:
            key = str(key)
            if key in seen:
                raise ValueError("duplicate immutable mapping key: {}".format(key))
            seen.add(key)
            normalized.append((key, _freeze_json(value)))
        normalized.sort(key=lambda item: item[0])
        self._items = tuple(normalized)
        self._dict = MappingProxyType(dict(normalized))

    def __getitem__(self, key):
        return self._dict[key]

    def __iter__(self) -> Iterator[str]:
        return iter(key for key, _ in self._items)

    def __len__(self):
        return len(self._items)

    def __hash__(self):
        return hash(self._items)

    def to_dict(self):
        return _thaw_json(self)


@dataclass(frozen=True)
class PlayerBoard:
    points: Tuple[int, ...]
    bar: int
    off: int

    def __post_init__(self):
        object.__setattr__(self, "points", tuple(self.points))

    def to_dict(self):
        return {"points": list(self.points), "bar": self.bar, "off": self.off}


@dataclass(frozen=True)
class CheckerCount:
    player_0: int
    player_1: int

    def to_dict(self):
        return {"player_0": self.player_0, "player_1": self.player_1}


@dataclass(frozen=True)
class Board:
    checker_count: CheckerCount
    player_0: PlayerBoard
    player_1: PlayerBoard
    coordinate_system: str = "self_relative_points"

    def to_dict(self):
        return {
            "coordinate_system": self.coordinate_system,
            "checker_count": self.checker_count.to_dict(),
            "player_0": self.player_0.to_dict(),
            "player_1": self.player_1.to_dict(),
        }


@dataclass(frozen=True)
class PositionState:
    game_state: str
    on_roll: Optional[str]
    decision_player: Optional[str]
    phase: str
    decision_type: str
    dice: Optional[Tuple[int, int]]

    def __post_init__(self):
        if self.dice is not None:
            object.__setattr__(self, "dice", tuple(self.dice))

    def to_dict(self):
        return {
            "game_state": self.game_state,
            "on_roll": self.on_roll,
            "decision_player": self.decision_player,
            "phase": self.phase,
            "decision_type": self.decision_type,
            "dice": list(self.dice) if self.dice is not None else None,
        }


@dataclass(frozen=True)
class PendingAction:
    type: str
    offerer: Optional[str]
    responder: Optional[str]
    offered_cube_value: Optional[int]
    resignation_multiplier: Optional[int]

    @classmethod
    def none(cls):
        return cls("none", None, None, None, None)

    @classmethod
    def unknown(cls):
        return cls("unknown", None, None, None, None)

    def to_dict(self):
        return {
            "type": self.type,
            "offerer": self.offerer,
            "responder": self.responder,
            "offered_cube_value": self.offered_cube_value,
            "resignation_multiplier": self.resignation_multiplier,
        }


@dataclass(frozen=True)
class CubeState:
    enabled: Optional[bool]
    value: Optional[int]
    owner: Optional[str]
    pending_action: PendingAction

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "value": self.value,
            "owner": self.owner,
            "pending_action": self.pending_action.to_dict(),
        }


@dataclass(frozen=True)
class MatchScore:
    player_0: int
    player_1: int
    match_length: int

    def to_dict(self):
        return {
            "player_0": self.player_0,
            "player_1": self.player_1,
            "match_length": self.match_length,
        }


@dataclass(frozen=True)
class RuleState:
    variation: Optional[str]
    crawford: Optional[bool]
    jacoby: Optional[bool]
    beavers: Optional[bool]
    raccoons: Optional[bool]
    automatic_doubles: Optional[int]
    maximum_cube: Optional[int]

    def to_dict(self):
        return {
            "variation": self.variation,
            "crawford": self.crawford,
            "jacoby": self.jacoby,
            "beavers": self.beavers,
            "raccoons": self.raccoons,
            "automatic_doubles": self.automatic_doubles,
            "maximum_cube": self.maximum_cube,
        }


@dataclass(frozen=True)
class UniversalPosition:
    board: Board
    state: PositionState
    cube: CubeState
    score: MatchScore
    rules: RuleState
    players: Tuple[str, str] = PLAYERS
    schema_version: str = UNIVERSAL_POSITION_VERSION

    def __post_init__(self):
        object.__setattr__(self, "players", tuple(self.players))

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "players": list(self.players),
            "board": self.board.to_dict(),
            "state": self.state.to_dict(),
            "cube": self.cube.to_dict(),
            "score": self.score.to_dict(),
            "rules": self.rules.to_dict(),
        }

    @classmethod
    def from_dict(cls, value):
        board = value["board"]
        state = value["state"]
        cube = value["cube"]
        score = value["score"]
        rules = value["rules"]
        return cls(
            schema_version=value.get("schema_version", UNIVERSAL_POSITION_VERSION),
            players=tuple(value.get("players", PLAYERS)),
            board=Board(
                coordinate_system=board["coordinate_system"],
                checker_count=CheckerCount(**board["checker_count"]),
                player_0=PlayerBoard(**board["player_0"]),
                player_1=PlayerBoard(**board["player_1"]),
            ),
            state=PositionState(
                game_state=state["game_state"],
                on_roll=state["on_roll"],
                decision_player=state["decision_player"],
                phase=state["phase"],
                decision_type=state["decision_type"],
                dice=tuple(state["dice"]) if state["dice"] is not None else None,
            ),
            cube=CubeState(
                enabled=cube["enabled"],
                value=cube["value"],
                owner=cube["owner"],
                pending_action=PendingAction(**cube["pending_action"]),
            ),
            score=MatchScore(**score),
            rules=RuleState(**rules),
        )


@dataclass(frozen=True)
class RawSource:
    kind: str
    value: Optional[str] = None
    media_type: Optional[str] = None
    sha256: Optional[str] = None
    uri: Optional[str] = None
    size_bytes: Optional[int] = None

    @classmethod
    def text(cls, value, media_type="text/plain"):
        return cls(kind="text", value=value, media_type=media_type)

    def to_dict(self):
        if self.kind == "text":
            data = {"kind": "text", "value": self.value}
            if self.media_type is not None:
                data["media_type"] = self.media_type
            return data
        data = {"kind": "artifact", "sha256": self.sha256, "media_type": self.media_type}
        if self.uri is not None:
            data["uri"] = self.uri
        if self.size_bytes is not None:
            data["size_bytes"] = self.size_bytes
        return data


@dataclass(frozen=True)
class ParserIdentity:
    name: str
    version: str
    runtime_version: Optional[str]

    def to_dict(self):
        return {
            "name": self.name,
            "version": self.version,
            "runtime_version": self.runtime_version,
        }


@dataclass(frozen=True)
class FieldOrigin:
    status: str
    source_path: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self):
        data = {"status": self.status}
        if self.source_path is not None:
            data["source_path"] = self.source_path
        if self.note is not None:
            data["note"] = self.note
        return data


@dataclass(frozen=True, order=True)
class ConversionLoss:
    field: str
    reason: str
    severity: str

    def to_dict(self):
        return {"field": self.field, "reason": self.reason, "severity": self.severity}


@dataclass(frozen=True)
class PositionSource:
    format: str
    profile: str
    raw_source: RawSource
    parser: ParserIdentity
    player_mapping: FrozenDict
    source_view_available: bool
    field_origins: FrozenDict
    external_settings: FrozenDict = field(default_factory=FrozenDict)
    assumptions: Tuple[str, ...] = field(default_factory=tuple)
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    conversion_losses: Tuple[ConversionLoss, ...] = field(default_factory=tuple)
    source_hash: Optional[str] = None
    position_schema_version: str = UNIVERSAL_POSITION_VERSION
    schema_version: str = POSITION_SOURCE_VERSION

    def __post_init__(self):
        object.__setattr__(self, "player_mapping", _freeze_json(self.player_mapping))
        origins = self.field_origins
        if not isinstance(origins, FrozenDict):
            origins = FrozenDict(
                (path, origin.to_dict() if isinstance(origin, FieldOrigin) else origin)
                for path, origin in origins.items()
            )
        object.__setattr__(self, "field_origins", origins)
        object.__setattr__(self, "external_settings", _freeze_json(self.external_settings))
        object.__setattr__(self, "assumptions", tuple(sorted(set(self.assumptions))))
        object.__setattr__(self, "warnings", tuple(sorted(set(self.warnings))))
        object.__setattr__(self, "conversion_losses", tuple(sorted(self.conversion_losses)))

    def to_dict(self, include_hash=True):
        data = {
            "schema_version": self.schema_version,
            "position_schema_version": self.position_schema_version,
            "format": self.format,
            "profile": self.profile,
            "raw_source": self.raw_source.to_dict(),
            "parser": self.parser.to_dict(),
            "player_mapping": self.player_mapping.to_dict(),
            "source_view_available": self.source_view_available,
            "field_origins": self.field_origins.to_dict(),
            "external_settings": self.external_settings.to_dict(),
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
            "conversion_losses": [item.to_dict() for item in self.conversion_losses],
        }
        if include_hash:
            data["source_hash"] = self.source_hash
        return data


@dataclass(frozen=True)
class BackgammonView:
    top_player: str
    bottom_player: str
    point_labels_for: str
    bottom_home_board_side: str
    cube_display_side: str
    rotation: str
    view_origin: str
    schema_version: str = BACKGAMMON_VIEW_VERSION

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "top_player": self.top_player,
            "bottom_player": self.bottom_player,
            "point_labels_for": self.point_labels_for,
            "bottom_home_board_side": self.bottom_home_board_side,
            "cube_display_side": self.cube_display_side,
            "rotation": self.rotation,
            "view_origin": self.view_origin,
        }


@dataclass(frozen=True)
class DecodedPosition:
    position: UniversalPosition
    source: PositionSource
    view: Optional[BackgammonView]
