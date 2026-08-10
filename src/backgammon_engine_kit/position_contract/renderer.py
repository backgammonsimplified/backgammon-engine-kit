"""Validated renderer-facing composition of position and view contracts."""

from __future__ import annotations

from dataclasses import dataclass

from ..serialization import canonical_json
from .decoders import decode_gnuid, decode_xgid
from .enrichment import enrich_position
from .hashing import semantic_state_hash as _semantic_state_hash
from .hashing import view_hash as _view_hash
from .models import BackgammonView, UniversalPosition
from .validation import (
    ContractValidationError,
    validate_backgammon_view,
    validate_universal_position,
)


class RendererPositionError(ContractValidationError):
    """Raised for an invalid or internally inconsistent renderer envelope."""


@dataclass(frozen=True)
class RendererPosition:
    """Immutable envelope for separate semantic and display contracts."""

    position: UniversalPosition
    semantic_state_hash: str
    view: BackgammonView
    view_hash: str

    def __post_init__(self):
        if not isinstance(self.position, UniversalPosition):
            raise RendererPositionError(
                "RendererPosition position must be a UniversalPosition"
            )
        if not isinstance(self.view, BackgammonView):
            raise RendererPositionError(
                "RendererPosition view must be a BackgammonView"
            )

        validate_universal_position(self.position)
        validate_backgammon_view(self.view)
        expected_semantic_hash = _semantic_state_hash(self.position)
        expected_view_hash = _view_hash(self.view)
        if self.semantic_state_hash != expected_semantic_hash:
            raise RendererPositionError(
                "RendererPosition semantic_state_hash does not match position"
            )
        if self.view_hash != expected_view_hash:
            raise RendererPositionError(
                "RendererPosition view_hash does not match view"
            )

    def to_dict(self):
        return {
            "position": self.position.to_dict(),
            "semantic_state_hash": self.semantic_state_hash,
            "view": self.view.to_dict(),
            "view_hash": self.view_hash,
        }


def default_backgammon_view():
    """Return the stable generated view used when a source supplies no view."""

    view = BackgammonView(
        top_player="player_0",
        bottom_player="player_1",
        point_labels_for="player_0",
        bottom_home_board_side="right",
        cube_display_side="left",
        rotation="default",
        view_origin="generated_default",
    )
    return validate_backgammon_view(view)


def create_renderer_position(position, view=None):
    """Compose a validated position with an explicit or default view."""

    if view is None:
        view = default_backgammon_view()
    validate_universal_position(position)
    validate_backgammon_view(view)
    return RendererPosition(
        position=position,
        semantic_state_hash=_semantic_state_hash(position),
        view=view,
        view_hash=_view_hash(view),
    )


def _enriched_position(decoded, external_settings):
    if external_settings is None:
        return decoded.position
    position, _source = enrich_position(
        decoded.position,
        decoded.source,
        external_settings,
    )
    return position


def renderer_position_from_xgid(
    raw_identifier,
    view=None,
    external_settings=None,
):
    """Decode a supported XGID and return a validated renderer envelope."""

    decoded = decode_xgid(raw_identifier)
    selected_view = decoded.view if view is None else view
    return create_renderer_position(
        _enriched_position(decoded, external_settings),
        selected_view,
    )


def renderer_position_from_gnuid(
    combined_id,
    view=None,
    external_settings=None,
    runtime_version="GNU Backgammon 1.08.003",
):
    """Decode a supported GNU combined ID and return a renderer envelope."""

    decoded = decode_gnuid(combined_id, runtime_version=runtime_version)
    return create_renderer_position(
        _enriched_position(decoded, external_settings),
        view,
    )


def renderer_position_json(renderer_position):
    """Return deterministic envelope JSON without a trailing newline."""

    if not isinstance(renderer_position, RendererPosition):
        raise RendererPositionError(
            "renderer_position_json requires a RendererPosition"
        )
    # Reconstructing verifies nested contracts and both object/hash bindings.
    RendererPosition(
        position=renderer_position.position,
        semantic_state_hash=renderer_position.semantic_state_hash,
        view=renderer_position.view,
        view_hash=renderer_position.view_hash,
    )
    return canonical_json(renderer_position.to_dict())
