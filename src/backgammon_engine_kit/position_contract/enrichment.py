"""Pure external enrichment for decoded Universal Position contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Iterable, Tuple

from .models import FrozenDict, PositionSource, UniversalPosition
from .semantics import derive_state
from .validation import (
    ContractValidationError,
    validate_origin_coverage,
    validate_position_source,
    validate_universal_position,
)


_DERIVED_ONLY_PREFIXES = (
    "/state/phase",
    "/state/decision_player",
    "/state/decision_type",
    "/cube/pending_action",
)


class EnrichmentError(ContractValidationError):
    """Raised when external context attempts an unsafe enrichment."""


def _escape(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _flatten(value: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _flatten(value[key], prefix + "/" + _escape(key))
    else:
        yield prefix, value


def _tokens(pointer: str) -> Tuple[str, ...]:
    if not pointer.startswith("/"):
        raise EnrichmentError("external setting path is not an absolute JSON Pointer: {}".format(pointer))
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/"))


def _get(data: Dict[str, Any], pointer: str) -> Any:
    value: Any = data
    for token in _tokens(pointer):
        if not isinstance(value, dict) or token not in value:
            raise EnrichmentError("external setting does not name a Universal Position field: {}".format(pointer))
        value = value[token]
    return value


def _set(data: Dict[str, Any], pointer: str, replacement: Any) -> None:
    tokens = _tokens(pointer)
    target: Any = data
    for token in tokens[:-1]:
        if not isinstance(target, dict) or token not in target:
            raise EnrichmentError("external setting does not name a Universal Position field: {}".format(pointer))
        target = target[token]
    if not isinstance(target, dict) or tokens[-1] not in target:
        raise EnrichmentError("external setting does not name a Universal Position field: {}".format(pointer))
    target[tokens[-1]] = replacement


def _deep_merge_settings(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in merged:
            if isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = _deep_merge_settings(merged[key], value)
            elif merged[key] != value:
                raise EnrichmentError("external setting conflicts with a previously recorded setting: /{}".format(_escape(key)))
        else:
            merged[key] = value
    return merged


def enrich_position(
    position: UniversalPosition,
    source: PositionSource,
    external_settings: Dict[str, Any],
) -> Tuple[UniversalPosition, PositionSource]:
    """Return enriched copies without mutating the raw decoded contracts.

    External settings may fill only schema fields whose current value is null.
    Directly represented, profile-fixed, and derived values are never overwritten.
    """

    validate_universal_position(position)
    validate_origin_coverage(position, source)
    if not isinstance(external_settings, dict):
        raise EnrichmentError("external settings must be an object")

    position_data = position.to_dict()
    source_data = source.to_dict()
    origins = source_data["field_origins"]
    existing_settings = source_data["external_settings"]

    for pointer, value in _flatten(external_settings):
        if not pointer:
            raise EnrichmentError("external settings must contain named Universal Position fields")
        if any(pointer == prefix or pointer.startswith(prefix + "/") for prefix in _DERIVED_ONLY_PREFIXES):
            raise EnrichmentError("external setting is derived-only: {}".format(pointer))
        current = _get(position_data, pointer)
        if current is not None:
            raise EnrichmentError(
                "external setting may fill only an unknown field: {} is already {!r}".format(pointer, current)
            )
        _set(position_data, pointer, value)
        origins[pointer] = {
            "status": "supplied_externally",
            "note": "Explicit external context supplied during enrichment.",
        }

    enriched_settings = _deep_merge_settings(existing_settings, external_settings)
    candidate = UniversalPosition.from_dict(position_data)
    candidate = derive_state(candidate)

    # Derivation can change these leaves after external rule context is applied.
    for pointer in ("/state/decision_player", "/state/phase", "/state/decision_type"):
        origins[pointer] = {
            "status": "derived",
            "note": "Derived after applying explicit external context.",
        }

    enriched_source = replace(
        source,
        field_origins=FrozenDict(origins),
        external_settings=FrozenDict(enriched_settings),
        source_hash=None,
    )
    validate_universal_position(candidate)
    validate_position_source(enriched_source)
    validate_origin_coverage(candidate, enriched_source)
    return candidate, enriched_source
