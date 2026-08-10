"""Validated canonical serialization and contract-specific hashes."""

from ..serialization import canonical_json, stable_hash
from .validation import (
    validate_backgammon_view,
    validate_position_source,
    validate_universal_position,
)


def semantic_state_json(position):
    validate_universal_position(position)
    return canonical_json(position.to_dict())


def semantic_state_hash(position):
    validate_universal_position(position)
    return stable_hash(position.to_dict())


def source_record_json(source):
    validate_position_source(source)
    return canonical_json(source.to_dict(include_hash=False))


def source_record_hash(source):
    validate_position_source(source)
    return stable_hash(source.to_dict(include_hash=False))


def view_json(view):
    validate_backgammon_view(view)
    return canonical_json(view.to_dict())


def view_hash(view):
    validate_backgammon_view(view)
    return stable_hash(view.to_dict())
