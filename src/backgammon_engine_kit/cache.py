"""Deterministic cache identity with explicit hit and miss outcomes."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .codec import result_from_dict
from .models import AnalysisResult
from .serialization import canonical_json, stable_hash


CACHE_LOOKUP_SCHEMA_VERSION = "analysis-cache-lookup-v1"


def cache_key(request):
    return "analysis-result-v2:" + stable_hash(request.cache_identity())


@dataclass(frozen=True)
class CacheLookup:
    outcome: str
    key: str
    result: Optional[AnalysisResult]
    schema_version: str = CACHE_LOOKUP_SCHEMA_VERSION

    def __post_init__(self):
        if self.outcome == "hit" and self.result is None:
            raise ValueError("cache hit requires a result")
        if self.outcome == "miss" and self.result is not None:
            raise ValueError("cache miss cannot contain a result")
        if self.outcome not in ("hit", "miss"):
            raise ValueError("cache outcome must be hit or miss")

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome,
            "key": self.key,
            "result": self.result.to_dict() if self.result is not None else None,
        }


class MemoryCache:
    def __init__(self):
        self._results = {}

    def lookup(self, request):
        key = cache_key(request)
        result = self._results.get(key)
        return CacheLookup("hit", key, result) if result is not None else CacheLookup("miss", key, None)

    def store(self, request, result):
        if result.status != "complete":
            raise ValueError("only successful analysis results may be cached")
        key = cache_key(request)
        self._results[key] = result
        return key


class FileCache:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key):
        digest = key.rsplit(":", 1)[-1]
        return self.root / "results" / (digest + ".json")

    def lookup(self, request):
        key = cache_key(request)
        path = self._path(key)
        if not path.is_file():
            return CacheLookup("miss", key, None)
        with path.open("r", encoding="utf-8") as handle:
            result = result_from_dict(json.load(handle))
        return CacheLookup("hit", key, result)

    def store(self, request, result):
        if result.status != "complete":
            raise ValueError("only successful analysis results may be cached")
        key = cache_key(request)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(canonical_json(result) + "\n", encoding="utf-8")
        temporary.replace(path)
        return key
