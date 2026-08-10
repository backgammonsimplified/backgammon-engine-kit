"""Pure semantic derivation for Universal Position state."""

from dataclasses import replace

from .models import PositionState, UniversalPosition


LEGAL = "legal"
ILLEGAL = "illegal"
UNKNOWN = "unknown"


def other_player(player):
    if player == "player_0":
        return "player_1"
    if player == "player_1":
        return "player_0"
    return None


def doubling_legality(position):
    """Return legal, illegal, or unknown for an ordinary pre-roll double."""
    if position.state.game_state != "playing":
        return ILLEGAL
    if position.state.dice is not None:
        return ILLEGAL
    if position.cube.pending_action.type != "none":
        return ILLEGAL
    if position.state.on_roll is None:
        return UNKNOWN
    if position.score.match_length > 0 and (
        position.score.player_0 >= position.score.match_length
        or position.score.player_1 >= position.score.match_length
    ):
        return ILLEGAL
    if position.cube.enabled is False:
        return ILLEGAL
    if position.rules.crawford is True:
        return ILLEGAL
    if position.cube.enabled is None or position.rules.crawford is None:
        return UNKNOWN
    if position.cube.value is None or position.cube.owner is None:
        return UNKNOWN
    if position.rules.maximum_cube is None:
        return UNKNOWN
    if position.cube.owner not in ("center", position.state.on_roll):
        return ILLEGAL
    if position.cube.value * 2 > position.rules.maximum_cube:
        return ILLEGAL
    return LEGAL


def _animal_availability(flag, match_length, offered_value, maximum_cube):
    if match_length > 0:
        return ILLEGAL
    if flag is False:
        return ILLEGAL
    if flag is None or maximum_cube is None or offered_value is None:
        return UNKNOWN
    if offered_value * 2 > maximum_cube:
        return ILLEGAL
    return LEGAL


def derive_state(position):
    """Return a new position with phase, decision player, and decision type derived."""
    game_state = position.state.game_state
    pending = position.cube.pending_action
    on_roll = position.state.on_roll
    dice = position.state.dice

    if game_state == "setup":
        phase = "setup"
        decision_player = None
        decision_type = "none"
    elif game_state in ("game_over", "resigned"):
        phase = "game_over"
        decision_player = None
        decision_type = "none"
    elif game_state != "playing":
        phase = "unknown"
        decision_player = None
        decision_type = "unknown"
    elif pending.type == "double":
        phase = "cube_response"
        decision_player = pending.responder
        availability = _animal_availability(
            position.rules.beavers,
            position.score.match_length,
            pending.offered_cube_value,
            position.rules.maximum_cube,
        )
        if availability == LEGAL:
            decision_type = "take_drop_or_beaver"
        elif availability == ILLEGAL:
            decision_type = "take_or_drop"
        else:
            decision_type = "unknown"
    elif pending.type == "beaver":
        phase = "beaver_response"
        decision_player = pending.responder
        availability = _animal_availability(
            position.rules.raccoons,
            position.score.match_length,
            pending.offered_cube_value,
            position.rules.maximum_cube,
        )
        if availability == LEGAL:
            decision_type = "take_drop_or_raccoon"
        elif availability == ILLEGAL:
            decision_type = "take_or_drop"
        else:
            decision_type = "unknown"
    elif pending.type == "raccoon":
        phase = "raccoon_response"
        decision_player = pending.responder
        decision_type = "take_or_drop"
    elif pending.type == "resignation":
        phase = "resignation_response"
        decision_player = pending.responder
        decision_type = "accept_or_reject_resignation"
    elif pending.type == "unknown":
        phase = "unknown"
        decision_player = None
        decision_type = "unknown"
    elif dice is not None:
        phase = "checker_play"
        decision_player = on_roll
        decision_type = "checker_play"
    else:
        phase = "pre_roll"
        decision_player = on_roll
        legality = doubling_legality(position)
        if legality == LEGAL:
            decision_type = "roll_or_double"
        elif legality == ILLEGAL:
            decision_type = "roll"
        else:
            decision_type = "unknown"

    state = PositionState(
        game_state=game_state,
        on_roll=on_roll,
        decision_player=decision_player,
        phase=phase,
        decision_type=decision_type,
        dice=dice,
    )
    return replace(position, state=state)
