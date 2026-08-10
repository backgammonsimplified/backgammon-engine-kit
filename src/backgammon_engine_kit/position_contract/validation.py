"""Strict JSON Schema and cross-field semantic validation."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .models import BackgammonView, PositionSource, UniversalPosition
from .semantics import derive_state


class ContractValidationError(ValueError):
    pass


_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_SCHEMA_FILES = {
    "universal-position-v1": "universal-position-v1.schema.json",
    "position-source-v1": "position-source-v1.schema.json",
    "backgammon-view-v1": "backgammon-view-v1.schema.json",
}
_VALIDATORS = {}


def _schema_validator(schema_version):
    if schema_version not in _VALIDATORS:
        path = _SCHEMA_DIR / _SCHEMA_FILES[schema_version]
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        _VALIDATORS[schema_version] = Draft202012Validator(schema)
    return _VALIDATORS[schema_version]


def _format_path(error):
    if not error.absolute_path:
        return "/"
    return "/" + "/".join(str(item).replace("~", "~0").replace("/", "~1") for item in error.absolute_path)


def validate_schema(value, schema_version):
    data = value.to_dict() if hasattr(value, "to_dict") else value
    errors = sorted(_schema_validator(schema_version).iter_errors(data), key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        raise ContractValidationError(
            "{} schema violation at {}: {}".format(schema_version, _format_path(first), first.message)
        )
    return value


def _is_power_of_two(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0 and value & (value - 1) == 0


def _semantic_error(message):
    raise ContractValidationError("universal-position-v1 semantic violation: " + message)


def validate_semantics(position):
    board = position.board
    counts = board.checker_count
    for player_name, player_board, expected in (
        ("player_0", board.player_0, counts.player_0),
        ("player_1", board.player_1, counts.player_1),
    ):
        total = sum(player_board.points) + player_board.bar + player_board.off
        if total != expected:
            _semantic_error("{} checker total {} does not equal checker_count {}".format(player_name, total, expected))

    for index in range(24):
        if board.player_0.points[index] and board.player_1.points[23 - index]:
            _semantic_error("both players occupy the same physical point at player_0 point {}".format(index + 1))

    cube = position.cube
    rules = position.rules
    score = position.score
    pending = cube.pending_action

    if cube.value is not None and not _is_power_of_two(cube.value):
        _semantic_error("cube.value must be a positive power of two")
    if rules.maximum_cube is not None and not _is_power_of_two(rules.maximum_cube):
        _semantic_error("rules.maximum_cube must be a positive power of two")
    if cube.value is not None and rules.maximum_cube is not None and cube.value > rules.maximum_cube:
        _semantic_error("accepted cube value exceeds known maximum cube")
    if cube.value == 1 and cube.owner not in ("center", None):
        _semantic_error("a one-cube cannot be owned")
    if cube.enabled is False and pending.type not in ("none", "unknown"):
        _semantic_error("cube action is pending while cube is known disabled")

    empty_fields = (
        pending.offerer,
        pending.responder,
        pending.offered_cube_value,
        pending.resignation_multiplier,
    )
    if pending.type in ("none", "unknown"):
        if any(value is not None for value in empty_fields):
            _semantic_error("{} pending action must not contain offer details".format(pending.type))
    elif pending.type == "resignation":
        if pending.offerer is None or pending.responder is None or pending.offerer == pending.responder:
            _semantic_error("resignation requires distinct offerer and responder")
        if pending.offered_cube_value is not None:
            _semantic_error("resignation must not contain an offered cube value")
        if pending.resignation_multiplier not in (1, 2, 3):
            _semantic_error("resignation multiplier must be 1, 2, or 3")
    else:
        if pending.offerer is None or pending.responder is None or pending.offerer == pending.responder:
            _semantic_error("{} requires distinct offerer and responder".format(pending.type))
        if pending.resignation_multiplier is not None:
            _semantic_error("cube action must not contain resignation multiplier")
        if cube.value is None or pending.offered_cube_value != cube.value * 2:
            _semantic_error("{} offered value must be twice the accepted cube value".format(pending.type))
        if rules.maximum_cube is not None and pending.offered_cube_value > rules.maximum_cube:
            _semantic_error("offered cube value exceeds known maximum cube")

    if pending.type == "double":
        if rules.crawford is True:
            _semantic_error("ordinary double is illegal during Crawford")
        if position.state.on_roll != pending.offerer:
            _semantic_error("ordinary double offerer must be the on-roll player")
        if cube.owner not in ("center", pending.offerer):
            _semantic_error("ordinary double offerer does not have cube access")
    elif pending.type == "beaver":
        if score.match_length > 0:
            _semantic_error("beaver is illegal in match play")
        if rules.beavers is False:
            _semantic_error("beaver is prohibited by known rules")
        if cube.owner != pending.offerer:
            _semantic_error("beaver offerer must own the accepted cube")
        if position.state.on_roll != pending.responder:
            _semantic_error("beaver responder must be the original on-roll doubler")
    elif pending.type == "raccoon":
        if score.match_length > 0:
            _semantic_error("raccoon is illegal in match play")
        if rules.raccoons is False:
            _semantic_error("raccoon is prohibited by known rules")
        if cube.owner != pending.responder:
            _semantic_error("raccoon responder must retain the accepted cube")
        if position.state.on_roll != pending.offerer:
            _semantic_error("raccoon offerer must be the original on-roll doubler")

    if score.match_length == 0:
        if rules.crawford is True:
            _semantic_error("Crawford cannot be true in a money game")
    else:
        if position.state.game_state == "playing" and (
            score.player_0 >= score.match_length or score.player_1 >= score.match_length
        ):
            _semantic_error("active match position has a player at or above match length")
        if rules.crawford is True:
            one_away_0 = score.player_0 == score.match_length - 1
            one_away_1 = score.player_1 == score.match_length - 1
            if one_away_0 == one_away_1:
                _semantic_error("Crawford requires exactly one player to be one-away")
        if rules.jacoby is True:
            _semantic_error("Jacoby is not a match-play rule")
        if rules.beavers is True or rules.raccoons is True:
            _semantic_error("animal redoubles are not permitted in match play")
        if rules.automatic_doubles not in (None, 0):
            _semantic_error("automatic doubles are not permitted in match play")

    derived = derive_state(position)
    if position.state != derived.state:
        _semantic_error(
            "state derivation mismatch: expected phase={}, decision_player={}, decision_type={}".format(
                derived.state.phase, derived.state.decision_player, derived.state.decision_type
            )
        )
    return position


def validate_universal_position(position):
    validate_schema(position, "universal-position-v1")
    return validate_semantics(position)


def _external_leaf_paths(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = prefix + "/" + escaped
            yield from _external_leaf_paths(item, child)
    else:
        yield prefix


def validate_position_source(source):
    validate_schema(source, "position-source-v1")
    forbidden = ("/state/phase", "/state/decision_player", "/state/decision_type", "/cube/pending_action")
    settings = source.external_settings.to_dict()
    for path in _external_leaf_paths(settings):
        if path in forbidden or any(path.startswith(item + "/") for item in forbidden):
            raise ContractValidationError("position-source-v1 external setting is derived-only: {}".format(path))
    for path in source.field_origins:
        if not path.startswith("/"):
            raise ContractValidationError("position-source-v1 field origin is not an absolute JSON Pointer: {}".format(path))
    return source


def _leaf_paths(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _leaf_paths(item, prefix + "/" + escaped)
    elif isinstance(value, list):
        # Arrays are semantic leaves for provenance purposes.
        yield prefix
    else:
        yield prefix


def validate_origin_coverage(position, source):
    validate_position_source(source)
    origins = source.field_origins.to_dict()
    for path in _leaf_paths(position.to_dict()):
        if path in ("/schema_version", "/players"):
            continue
        covered = path in origins or any(path.startswith(origin + "/") for origin in origins)
        if not covered:
            raise ContractValidationError("position-source-v1 has no origin covering {}".format(path))
    for path in _external_leaf_paths(source.external_settings.to_dict()):
        origin = origins.get(path)
        if origin is None or origin.get("status") != "supplied_externally":
            raise ContractValidationError("external setting lacks supplied_externally origin: {}".format(path))
    return source


def validate_backgammon_view(view):
    validate_schema(view, "backgammon-view-v1")
    if view.top_player == view.bottom_player:
        raise ContractValidationError("backgammon-view-v1 top and bottom players must differ")
    return view


def validate_contract(value):
    if isinstance(value, UniversalPosition):
        return validate_universal_position(value)
    if isinstance(value, PositionSource):
        return validate_position_source(value)
    if isinstance(value, BackgammonView):
        return validate_backgammon_view(value)
    raise TypeError("unsupported contract type: {}".format(type(value).__name__))
