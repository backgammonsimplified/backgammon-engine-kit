"""Deterministic JSON, hashing, and public-safety helpers."""

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping


_PRIVATE_PATH = re.compile(
    r"(?:^|[\s\"'])(?:/(?:home|users|private|var/tmp)/[^\s\"']+|[A-Za-z]:\\Users\\[^\s\"']+)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|password|secret)[\"']?\s*[:=]\s*[\"']?[^\s,;\"']{4,}",
    re.IGNORECASE,
)


def to_primitive(value):
    """Return a JSON-compatible value without dropping explicit nulls."""
    if hasattr(value, "to_dict"):
        return to_primitive(value.to_dict())
    if dataclasses.is_dataclass(value):
        return to_primitive(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported value for deterministic JSON: {}".format(type(value).__name__))


def canonical_json(value):
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_public_safe(value, label="public value"):
    """Reject private absolute paths and common inline secret assignments."""
    text = canonical_json(value)
    if _PRIVATE_PATH.search(text):
        raise ValueError("{} contains a private absolute path".format(label))
    if _SECRET_VALUE.search(text):
        raise ValueError("{} contains a secret-like value".format(label))
    return value
