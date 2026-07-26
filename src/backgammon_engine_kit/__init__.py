"""Public contracts for reusable backgammon position analysis."""

from .cache import CacheLookup, FileCache, MemoryCache, cache_key
from .capabilities import capability_report
from .models import (
    ANALYSIS_SETTINGS,
    RESULT_SCHEMA_VERSION,
    AnalysisRequest,
    AnalysisResult,
    CheckerCandidate,
    CheckerDecision,
    ConfigurationTrace,
    CubeAction,
    CubeDecision,
    EngineConfiguration,
    EngineFailure,
    MoveFilter,
    NormalizedPosition,
    OutcomeProbabilities,
    Position,
    RawSource,
)
from .serialization import canonical_json

from .position_contract import (
    BackgammonView,
    PositionSource,
    UniversalPosition,
    canonical_to_bgsage,
    decode_gnuid,
    decode_xgid,
    enrich_position,
    semantic_state_hash,
    source_record_hash,
    verify_gnu_source_bridge,
    view_hash,
)

__all__ = [
    "ANALYSIS_SETTINGS",
    "BackgammonView",
    "PositionSource",
    "UniversalPosition",
    "canonical_to_bgsage",
    "decode_gnuid",
    "decode_xgid",
    "enrich_position",
    "semantic_state_hash",
    "source_record_hash",
    "verify_gnu_source_bridge",
    "view_hash",
    "RESULT_SCHEMA_VERSION",
    "AnalysisRequest",
    "AnalysisResult",
    "CacheLookup",
    "CheckerCandidate",
    "CheckerDecision",
    "ConfigurationTrace",
    "CubeAction",
    "CubeDecision",
    "EngineConfiguration",
    "EngineFailure",
    "FileCache",
    "MemoryCache",
    "MoveFilter",
    "NormalizedPosition",
    "OutcomeProbabilities",
    "Position",
    "RawSource",
    "cache_key",
    "canonical_json",
    "capability_report",
]

__version__ = "0.3.0"
