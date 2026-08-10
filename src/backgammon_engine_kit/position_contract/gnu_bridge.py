"""Guarded reuse of a preserved combined GNU ID."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional

from .decoders import decode_gnuid
from .enrichment import enrich_position
from .hashing import semantic_state_hash, source_record_hash
from .models import PositionSource, UniversalPosition
from .validation import validate_origin_coverage, validate_position_source, validate_universal_position


class GnuSourceBridgeError(ValueError):
    """Raised when preserved GNU source cannot be proven equivalent."""


def with_source_hash(source: PositionSource) -> PositionSource:
    """Return an immutable source record carrying its validated projection hash."""
    validate_position_source(source)
    return replace(source, source_hash=source_record_hash(source))


def _lookup(position: UniversalPosition, pointer: str) -> Any:
    value: Any = position.to_dict()
    for token in pointer.strip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or token not in value:
            raise GnuSourceBridgeError("invocation setting does not name a canonical field: {}".format(pointer))
        value = value[token]
    return value


def verify_gnu_source_bridge(
    position: UniversalPosition,
    source: PositionSource,
    expected_semantic_hash: Optional[str] = None,
    invocation_settings: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the preserved GNU ID only after complete semantic verification."""

    validate_universal_position(position)
    validate_origin_coverage(position, source)
    if source.format != "gnuid":
        raise GnuSourceBridgeError("GNU source bridge requires position-source format=gnuid")
    if source.raw_source.kind != "text" or not source.raw_source.value:
        raise GnuSourceBridgeError("GNU source bridge requires a preserved text combined GNU ID")
    if source.profile != "gnubg-combined-id-v1-15-checker":
        raise GnuSourceBridgeError("GNU source bridge profile mismatch")
    if source.parser.name != "bms-gnuid-adapter-v1" or source.parser.version != "1.0.0":
        raise GnuSourceBridgeError("GNU source bridge parser identity mismatch")
    if source.source_hash is not None and source.source_hash != source_record_hash(source):
        raise GnuSourceBridgeError("GNU source record hash mismatch")

    decoded = decode_gnuid(source.raw_source.value, runtime_version=source.parser.runtime_version)
    if decoded.source.profile != source.profile:
        raise GnuSourceBridgeError("redecoded GNU profile mismatch")
    if decoded.source.player_mapping != source.player_mapping:
        raise GnuSourceBridgeError("redecoded GNU player mapping mismatch")

    reconstructed_position = decoded.position
    reconstructed_source = decoded.source
    external_settings = source.external_settings.to_dict()
    if external_settings:
        reconstructed_position, reconstructed_source = enrich_position(
            reconstructed_position,
            reconstructed_source,
            external_settings,
        )

    validate_universal_position(reconstructed_position)
    validate_origin_coverage(reconstructed_position, reconstructed_source)
    target_hash = expected_semantic_hash or semantic_state_hash(position)
    reconstructed_hash = semantic_state_hash(reconstructed_position)
    if reconstructed_hash != target_hash:
        raise GnuSourceBridgeError("preserved GNU ID does not match the request semantic hash")
    if semantic_state_hash(position) != target_hash:
        raise GnuSourceBridgeError("request position does not match the expected semantic hash")

    for pointer, expected in sorted((invocation_settings or {}).items()):
        actual = _lookup(position, pointer)
        if actual != expected:
            raise GnuSourceBridgeError(
                "GNU invocation setting mismatch at {}: canonical={!r}, invocation={!r}".format(
                    pointer, actual, expected
                )
            )

    return source.raw_source.value
