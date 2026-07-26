"""Final-boundary conversion from stable canonical state to BGSage context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .models import UniversalPosition
from .semantics import other_player
from .validation import validate_universal_position


class BGSageConversionError(ValueError):
    """Raised when canonical state cannot be represented without a default."""


@dataclass(frozen=True)
class BGSagePosition:
    board: Tuple[int, ...]
    on_roll: str
    dice: Optional[Tuple[int, int]]
    cube_enabled: bool
    cube_value: int
    cube_owner: str
    match_length: int
    player_score: int
    opponent_score: int
    player_away: int
    opponent_away: int
    crawford: bool
    jacoby: bool
    beaver: bool
    player_off: int
    opponent_off: int

    def to_dict(self):
        return {
            "board": list(self.board),
            "on_roll": self.on_roll,
            "dice": list(self.dice) if self.dice is not None else None,
            "cube_enabled": self.cube_enabled,
            "cube_value": self.cube_value,
            "cube_owner": self.cube_owner,
            "match_length": self.match_length,
            "player_score": self.player_score,
            "opponent_score": self.opponent_score,
            "player_away": self.player_away,
            "opponent_away": self.opponent_away,
            "crawford": self.crawford,
            "jacoby": self.jacoby,
            "beaver": self.beaver,
            "player_off": self.player_off,
            "opponent_off": self.opponent_off,
        }


def _required(path, value):
    if value is None:
        raise BGSageConversionError(
            "cannot populate BGSage field from unresolved canonical field {}".format(path)
        )
    return value


def canonical_to_bgsage(position: UniversalPosition) -> BGSagePosition:
    """Convert canonical stable players to BGSage's current-player-relative board.

    Board indices 1..24 contain the current player's self-relative points as
    positive values. The opponent appears as negative values on mirrored
    points. Index 25 is the current player's bar; index 0 is the opponent bar.
    """

    validate_universal_position(position)
    current = _required("/state/on_roll", position.state.on_roll)
    opponent = other_player(current)
    if opponent is None:
        raise BGSageConversionError("BGSage requires an established on-roll player")

    enabled = _required("/cube/enabled", position.cube.enabled)
    cube_value = _required("/cube/value", position.cube.value)
    owner = _required("/cube/owner", position.cube.owner)
    variation = _required("/rules/variation", position.rules.variation)
    crawford = _required("/rules/crawford", position.rules.crawford)
    if variation != "standard":
        raise BGSageConversionError("BGSage boundary supports only variation=standard")

    if position.score.match_length == 0:
        jacoby = _required("/rules/jacoby", position.rules.jacoby)
        beaver = _required("/rules/beavers", position.rules.beavers)
    else:
        # Match-play false values are semantic requirements, not defaults.
        jacoby = _required("/rules/jacoby", position.rules.jacoby)
        beaver = _required("/rules/beavers", position.rules.beavers)

    current_board = getattr(position.board, current)
    opponent_board = getattr(position.board, opponent)
    board = [0] * 26
    board[25] = current_board.bar
    board[0] = -opponent_board.bar
    for own_point in range(1, 25):
        current_count = current_board.points[own_point - 1]
        opponent_count = opponent_board.points[24 - own_point]
        if current_count and opponent_count:
            raise BGSageConversionError("canonical board contains a physical-point collision")
        board[own_point] = current_count if current_count else -opponent_count

    if owner == "center":
        relative_owner = "centered"
    elif owner == current:
        relative_owner = "player"
    elif owner == opponent:
        relative_owner = "opponent"
    else:
        raise BGSageConversionError("unsupported canonical cube owner: {}".format(owner))

    player_score = getattr(position.score, current)
    opponent_score = getattr(position.score, opponent)
    match_length = position.score.match_length
    player_away = match_length - player_score if match_length else 0
    opponent_away = match_length - opponent_score if match_length else 0

    return BGSagePosition(
        board=tuple(board),
        on_roll=current,
        dice=position.state.dice,
        cube_enabled=enabled,
        cube_value=cube_value,
        cube_owner=relative_owner,
        match_length=match_length,
        player_score=player_score,
        opponent_score=opponent_score,
        player_away=player_away,
        opponent_away=opponent_away,
        crawford=crawford,
        jacoby=jacoby,
        beaver=beaver,
        player_off=current_board.off,
        opponent_off=opponent_board.off,
    )
