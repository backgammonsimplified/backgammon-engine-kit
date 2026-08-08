from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdentifierConversion:
    xgid: str | None
    complete_gnuid: str | None
    position_id: str | None
    match_id: str | None


@dataclass(frozen=True)
class ExportedPosition:
    board: str
    details: str


@dataclass
class GalleryDirection:
    case_id: str
    direction: str
    source_identifier: str
    converted_identifier: str
    roundtrip_identifier: str
    retained_identifier: str | None
    retained_oracle_identifier: str | None
    live_oracle_identifier: str | None
    source_state: dict[str, Any]
    converted_state: dict[str, Any]
    roundtrip_state: dict[str, Any]
    source_semantic: dict[str, Any]
    converted_semantic: dict[str, Any]
    roundtrip_semantic: dict[str, Any]
    source_cli: dict[str, Any]
    converted_cli: dict[str, Any]
    roundtrip_cli: dict[str, Any]
    retained_source_cli: dict[str, Any]
    retained_converted_cli: dict[str, Any]
    source_renderer: dict[str, Any] | None
    converted_renderer: dict[str, Any] | None
    roundtrip_renderer: dict[str, Any] | None
    parity: str
    limitations: str
    conversion_hard_differences: list[str]
    conversion_representational_differences: list[str]
    roundtrip_hard_differences: list[str]
    roundtrip_representational_differences: list[str]
    roundtrip_identifier_exact: bool
    retained_oracle_status: str
    live_oracle_status: str
    r_comparison: dict[str, Any] | None = None
