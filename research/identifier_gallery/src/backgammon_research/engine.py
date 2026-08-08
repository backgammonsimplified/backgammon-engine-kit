from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path
from typing import Any, Mapping

from .models import IdentifierConversion


class BridgePreparationUnavailable(RuntimeError):
    """An explicit non-error status returned by the public analysis bridge."""

    def __init__(
        self,
        status: str,
        *,
        missing_state: tuple[str, ...] = (),
        unsupported_state: tuple[str, ...] = (),
    ) -> None:
        if status not in {"unsupported", "unavailable"}:
            raise ValueError(f"unexpected bridge availability status: {status}")
        self.status = status
        self.missing_state = missing_state
        self.unsupported_state = unsupported_state
        super().__init__(
            "Engine Kit public bridge could not prepare GNUID: "
            f"status={status} missing={missing_state} unsupported={unsupported_state}"
        )


def _value(value: Any) -> Any:
    if hasattr(value, "value"):
        return _value(value.value)
    if isinstance(value, tuple):
        return [_value(item) for item in value]
    if isinstance(value, list):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    return value


def _split_gnuid(value: str) -> tuple[str, str]:
    pieces = value.strip().split(":")
    if len(pieces) != 2 or not all(pieces):
        raise ValueError(f"complete GNUID required, received: {value!r}")
    return pieces[0], pieces[1]


def _other_player(player: str) -> str:
    if player == "player_x":
        return "player_o"
    if player == "player_o":
        return "player_x"
    raise ValueError(f"unsupported player label: {player!r}")


def _mapped_player(value: Any, mapping: Mapping[str, str]) -> Any:
    if value in ("player_x", "player_o"):
        return mapping.get(str(value), str(value))
    return value


def compare_semantic_views(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Return hard semantic differences and representational rule differences."""

    hard_paths = (
        "position.points",
        "position.x_off",
        "position.o_off",
        "state.on_roll",
        "state.dice",
        "state.cube_value",
        "state.cube_owner",
        "state.score_x",
        "state.score_o",
        "state.match_length",
    )
    rule_paths = (
        "rules.crawford",
        "rules.jacoby",
        "rules.beavers",
    )

    def get(value: dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        return current

    hard = [path for path in hard_paths if get(left, path) != get(right, path)]
    losses = [path for path in rule_paths if get(left, path) != get(right, path)]
    return hard, losses


class EngineKitResearchAdapter:
    """Small read-only adapter over one explicitly selected Engine Kit source."""

    def __init__(self, engine_kit_src: Path | None = None):
        self.requested_src = engine_kit_src.resolve() if engine_kit_src else None
        if self.requested_src is not None:
            for name in tuple(sys.modules):
                if name == "backgammon_engine_kit" or name.startswith(
                    "backgammon_engine_kit."
                ):
                    del sys.modules[name]
            sys.path.insert(0, str(self.requested_src))

        self.bek = importlib.import_module("backgammon_engine_kit")
        self.bridge = importlib.import_module("backgammon_engine_kit.identifier_bridge")

        module_file = Path(self.bek.__file__).resolve()
        if self.requested_src is not None and not module_file.is_relative_to(
            self.requested_src
        ):
            raise RuntimeError(
                "Engine Kit import did not come from requested source: "
                f"requested={self.requested_src}, imported={module_file}"
            )
        self.provenance = {
            "requested_src": str(self.requested_src) if self.requested_src else None,
            "imported_module": str(module_file),
        }

    def parse(self, identifier: str) -> Any:
        return self.bek.parse_analysis_identifier(identifier)

    def canonical_view(self, identifier: str) -> dict[str, Any]:
        parsed_dict = self.parse(identifier).to_dict()
        return {
            "raw_identifier": parsed_dict["raw_identifier"],
            "identifier_format": parsed_dict["identifier_format"],
            "canonical_position": parsed_dict["canonical_position"],
            "state": parsed_dict["state"],
            "source_turn": parsed_dict["source_turn"],
            "source_orientation": parsed_dict["source_orientation"],
            "source_player_mapping": parsed_dict["source_player_mapping"],
            "canonical_player_mapping": parsed_dict["canonical_player_mapping"],
            "normalization_applied": parsed_dict["normalization_applied"],
            "point_reversal_applied": parsed_dict["point_reversal_applied"],
            "bar_reversal_applied": parsed_dict["bar_reversal_applied"],
            "unavailable_state": parsed_dict["unavailable_state"],
            "unsupported_state": parsed_dict["unsupported_state"],
        }

    def semantic_view(
        self,
        identifier: str,
        *,
        player_mapping: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return the small state that should survive identifier conversion.

        `player_mapping` maps the identifier-local X/O labels into the source
        comparison namespace. Checker placement stays in Engine Kit's fixed
        physical point coordinates; only player-labelled match fields move.
        """

        parsed = self.parse(identifier).to_dict()
        position = parsed["canonical_position"]
        state = parsed["state"]
        mapping = dict(player_mapping or {"player_x": "player_x", "player_o": "player_o"})

        scores: dict[str, Any] = {}
        scores[mapping["player_x"]] = state["score_x"]
        scores[mapping["player_o"]] = state["score_o"]
        match_length = state["match_length"]

        return {
            "position": {
                "points": position["points"],
                "x_off": position["x_off"],
                "o_off": position["o_off"],
            },
            "state": {
                "on_roll": _mapped_player(state["on_roll"], mapping),
                "dice": state["dice"],
                "cube_value": state["cube_value"],
                "cube_owner": _mapped_player(state["cube_owner"], mapping),
                "score_x": scores.get("player_x"),
                "score_o": scores.get("player_o"),
                "match_length": match_length,
            },
            "rules": {
                "crawford": True if (match_length and state["crawford"] is True) else None,
                "jacoby": True if (match_length == 0 and state["jacoby"] is True) else None,
                "beavers": True if (match_length == 0 and bool(state["beavers"])) else None,
            },
        }

    @staticmethod
    def xgid_target_mapping(source_view: dict[str, Any]) -> dict[str, str]:
        current = source_view["state"]["on_roll"]
        return {"player_x": current, "player_o": _other_player(current)}

    def xgid_to_gnuid(self, xgid: str) -> IdentifierConversion:
        parsed = self.parse(xgid)
        decision = "checker" if parsed.state.dice else "cube"
        prepared = self.bek.to_gnu_request(xgid, decision)
        if not prepared.ready or not prepared.engine_identifier:
            if prepared.status in {"unsupported", "unavailable"}:
                raise BridgePreparationUnavailable(
                    prepared.status,
                    missing_state=prepared.missing_state,
                    unsupported_state=prepared.unsupported_state,
                )
            raise ValueError(
                "Engine Kit public bridge returned an unexpected incomplete result: "
                f"status={prepared.status} engine_identifier={prepared.engine_identifier!r}"
            )
        gnuid = str(prepared.engine_identifier)
        position_id, match_id = _split_gnuid(gnuid)
        return IdentifierConversion(
            xgid=xgid,
            complete_gnuid=gnuid,
            position_id=position_id,
            match_id=match_id,
        )

    def gnuid_to_xgid(
        self,
        gnuid: str,
        *,
        jacoby: bool = False,
        beavers: bool = False,
        action: str | None = None,
        max_cube: int = 1024,
    ) -> IdentifierConversion:
        if jacoby or beavers or action is not None or max_cube != 1024:
            pass
        xgid = str(self.bek.gnuid_to_xgid(gnuid))
        position_id, match_id = _split_gnuid(gnuid)
        return IdentifierConversion(
            xgid=xgid,
            complete_gnuid=gnuid,
            position_id=position_id,
            match_id=match_id,
        )
