from __future__ import annotations

import argparse
import csv
import importlib
import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .calculator_reference import BackgammonCalculatorReference
from .engine import EngineKitResearchAdapter
from .evidence import split_exported_position
from .gallery_html import e, method_card, reference_card, render_page
from .gnu_cli import GnuBackgammonCli
from .models import RESULT_CLASSIFICATIONS
from .r_oracle import BglabGnuidOracle
from .renderer import BackgammonBoardRenderer


XGID_DIRECTION = "XGID \u2192 GNUID \u2192 XGID"
GNUID_DIRECTION = "GNUID \u2192 XGID \u2192 GNUID"
LABELS = {
    "native_python": "Engine Kit native",
    "engine_kit": "Engine Kit public API / bridge",
    "ankigammon_direct": "Direct AnkiGammon",
}
SURFACE_ORDER = ("native_python", "engine_kit", "ankigammon_direct")


@dataclass(frozen=True)
class Case:
    case_id: str
    label: str
    xgid: str
    gnuid: str


def load_cases(path: Path, case_id: str | None = None) -> list[Case]:
    with path.open(newline="", encoding="utf-8") as handle:
        cases = [Case(**row) for row in csv.DictReader(handle)]
    if case_id is None:
        return cases
    selected = [case for case in cases if case.case_id == case_id]
    if not selected:
        raise ValueError(f"fixture case not found: {case_id}")
    return selected


def _bek():
    return importlib.import_module("backgammon_engine_kit")


def _engine_factual(identifier: str) -> dict[str, Any]:
    bek = _bek()
    position = (
        bek.position_from_xgid(identifier)
        if identifier.startswith("XGID=")
        else bek.position_from_gnuid(identifier)
    ).to_dict()
    board = position["board"]
    player_0 = board["player_0"]
    player_1 = board["player_1"]
    state = position["state"]
    cube = position["cube"]
    score = position["score"]
    rules = position["rules"]
    pending_type = cube.get("pending_action", {}).get("type")
    action = pending_type if pending_type and pending_type != "none" else "roll"
    cube_owner = cube.get("owner")
    if cube_owner == "centered":
        cube_owner = "center"
    return {
        "stable_player_identity": {
            "player_0": "player_0",
            "player_1": "player_1",
        },
        "checker_points": {
            # Engine Kit stores player_0 in self-relative point order. Reverse
            # that row into Calculator/Board fixed XGID physical coordinates.
            "player_0": list(reversed(player_0["points"])),
            "player_1": list(player_1["points"]),
        },
        "bars": {
            "player_0": player_0["bar"],
            "player_1": player_1["bar"],
        },
        "borne_off": {
            "player_0": player_0["off"],
            "player_1": player_1["off"],
        },
        "state": {
            "on_roll": state.get("on_roll"),
            "decision_player": state.get("decision_player"),
            "action": action,
            "dice": state.get("dice"),
        },
        "cube": {
            "value": cube.get("value"),
            "owner": cube_owner,
        },
        "score": {
            "player_0": score.get("player_0"),
            "player_1": score.get("player_1"),
            "match_length": score.get("match_length"),
        },
        "rules": {
            "crawford": rules.get("crawford"),
            "jacoby": rules.get("jacoby"),
            "beavers": rules.get("beavers"),
            "maximum_cube": rules.get("maximum_cube"),
        },
    }


def _canonical_safe(identifier: str) -> dict[str, Any] | None:
    try:
        return _engine_factual(identifier)
    except Exception:
        return None


def _comparison_state(value: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(value))
    # GNUID has no XGID maximum-cube field. It is displayed, but the accepted
    # contract classifies that default normalization separately from facts.
    normalized.get("rules", {}).pop("maximum_cube", None)
    return normalized


def _states_factually_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _comparison_state(left) == _comparison_state(right)


def _same(left: str, right: str) -> bool:
    try:
        return _states_factually_equal(_engine_factual(left), _engine_factual(right))
    except Exception:
        return False


def _flat(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    out: dict[str, Any] = {}
    for key, child in value.items():
        out.update(_flat(child, f"{prefix}.{key}" if prefix else str(key)))
    return out


def _state_diff(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    left_flat, right_flat = _flat(left), _flat(right)
    return [
        {"path": path, "left": left_flat.get(path), "right": right_flat.get(path)}
        for path in sorted(set(left_flat) | set(right_flat))
        if left_flat.get(path) != right_flat.get(path)
    ]


def _diff(left: str, right: str) -> list[dict[str, Any]]:
    try:
        return _state_diff(_engine_factual(left), _engine_factual(right))
    except Exception as exc:
        return [{"path": "decode", "left": "", "right": str(exc)}]


def _classification(exact: bool, factual: bool) -> str:
    if exact:
        return RESULT_CLASSIFICATIONS[0]
    if factual:
        return RESULT_CLASSIFICATIONS[1]
    return RESULT_CLASSIFICATIONS[2]


class NativeSurface:
    name = "native_python"

    def xgid_to_gnuid(self, xgid: str) -> str:
        return str(_bek().xgid_to_gnuid(xgid, allow_lossy=True))

    def gnuid_to_xgid(self, gnuid: str) -> str:
        return str(_bek().gnuid_to_xgid(gnuid))


class BridgeSurface:
    name = "engine_kit"

    def __init__(self) -> None:
        self.adapter = EngineKitResearchAdapter()

    def xgid_to_gnuid(self, xgid: str) -> str:
        result = self.adapter.xgid_to_gnuid(xgid)
        if not result.complete_gnuid:
            raise ValueError("Engine Kit public bridge returned no GNUID")
        return result.complete_gnuid

    def gnuid_to_xgid(self, gnuid: str) -> str:
        result = self.adapter.gnuid_to_xgid(gnuid)
        if not result.xgid:
            raise ValueError("Engine Kit public API returned no XGID")
        return result.xgid


class AnkiSurface:
    name = "ankigammon_direct"

    def __init__(self) -> None:
        self.xgid = importlib.import_module("ankigammon.utils.xgid")
        self.gnuid = importlib.import_module("ankigammon.utils.gnuid")
        self.models = importlib.import_module("ankigammon.models")
        try:
            self.version = importlib.metadata.version("ankigammon")
        except importlib.metadata.PackageNotFoundError:
            self.version = "source-tree"

    def xgid_to_gnuid(self, xgid: str) -> str:
        position, metadata = self.xgid.parse_xgid(xgid)
        match_length = int(metadata.get("match_length", 0))
        return str(
            self.gnuid.encode_gnuid(
                position,
                cube_value=int(metadata.get("cube_value", 1)),
                cube_owner=metadata.get("cube_owner", self.models.CubeState.CENTERED),
                dice=metadata.get("dice"),
                on_roll=metadata.get("on_roll", self.models.Player.X),
                score_x=int(metadata.get("score_x", 0)),
                score_o=int(metadata.get("score_o", 0)),
                match_length=match_length,
                crawford=(
                    bool(int(metadata.get("crawford_jacoby", 0)) & 1)
                    if match_length
                    else False
                ),
            )
        )

    def gnuid_to_xgid(self, gnuid: str) -> str:
        position, metadata = self.gnuid.parse_gnuid(gnuid)
        match_length = int(metadata.get("match_length", 0))
        return str(
            self.xgid.encode_xgid(
                position,
                cube_value=int(metadata.get("cube_value", 1)),
                cube_owner=metadata.get("cube_owner", self.models.CubeState.CENTERED),
                dice=metadata.get("dice"),
                on_roll=metadata.get("on_roll", self.models.Player.O),
                score_x=int(metadata.get("score_x", 0)),
                score_o=int(metadata.get("score_o", 0)),
                match_length=match_length,
                crawford_jacoby=(
                    1 if match_length and bool(metadata.get("crawford")) else 0
                ),
                max_cube=1024,
            )
        )


def _attempt(
    name: str,
    direction: str,
    source: str,
    reference: str,
    convert: Callable[[str], str],
    returner: Callable[[str], str],
) -> dict[str, Any]:
    try:
        middle = convert(source)
        terminal = returner(middle)
        exact = middle == reference
        factual = _same(middle, reference)
        return {
            "surface": name,
            "label": LABELS[name],
            "direction": direction,
            "source": source,
            "middle": middle,
            "terminal": terminal,
            "status": "ok",
            "error": None,
            "reference_exact": exact,
            "reference_semantic": factual,
            "classification": _classification(exact, factual),
            "roundtrip_exact": terminal == source,
            "roundtrip_semantic": _same(terminal, source),
            "middle_diff_from_reference": _diff(reference, middle),
            "roundtrip_diff_from_source": _diff(source, terminal),
        }
    except (ModuleNotFoundError, NotImplementedError) as exc:
        classification = RESULT_CLASSIFICATIONS[3]
        status = "unavailable"
        error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        classification = RESULT_CLASSIFICATIONS[4]
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "surface": name,
        "label": LABELS[name],
        "direction": direction,
        "source": source,
        "middle": None,
        "terminal": None,
        "status": status,
        "error": error,
        "reference_exact": False,
        "reference_semantic": False,
        "classification": classification,
        "roundtrip_exact": False,
        "roundtrip_semantic": False,
        "middle_diff_from_reference": [],
        "roundtrip_diff_from_source": [],
    }


def _gnu(gnu: Any, identifier: str, scratch: Path, key: str) -> dict[str, Any] | None:
    if not identifier:
        return None
    try:
        record = gnu.load(identifier, scratch, key)
        exported = split_exported_position(record.get("exported_text"))
        return {
            **record,
            "evidence_source": "real GNU Backgammon CLI",
            "board": exported.board,
            "details": exported.details,
        }
    except Exception as exc:
        return {
            "input": identifier,
            "error": f"{type(exc).__name__}: {exc}",
            "board": "GNU CLI evidence unavailable.",
        }


def _render(renderer: Any, identifier: str, directory: Path, key: str):
    if not identifier or not identifier.startswith("XGID="):
        return None
    try:
        return renderer.render(identifier, directory, key)
    except Exception as exc:
        return {
            "input": identifier,
            "type": "unavailable",
            "output": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def _calculator_canonical(calculator: Any, identifier: str) -> dict[str, Any] | None:
    try:
        return calculator.canonical_position(identifier)
    except Exception:
        return None


def _board_direct_gnuid_parity(
    renderer: Any,
    gnuid: str,
    converted_xgid: str,
    renders: Path,
    name: str,
    converted_record: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        direct_record = renderer.render_gnuid(gnuid, renders, f"{name}-direct-gnuid")
        path_a_state = (converted_record or {}).get("factual_state")
        path_b_state = direct_record.get("factual_state")
        if not path_a_state or not path_b_state:
            classification = RESULT_CLASSIFICATIONS[3]
            differences: list[dict[str, Any]] = []
        elif path_a_state == path_b_state:
            classification = RESULT_CLASSIFICATIONS[0]
            differences = []
        elif _states_factually_equal(path_a_state, path_b_state):
            classification = RESULT_CLASSIFICATIONS[1]
            differences = _state_diff(path_a_state, path_b_state)
        else:
            classification = RESULT_CLASSIFICATIONS[2]
            differences = _state_diff(path_a_state, path_b_state)
        return {
            "consumer": "backgammonboard v0.1.1",
            "role": "renderer/consumer diagnostic; not conversion authority",
            "complete_gnuid": gnuid,
            "path_a": {
                "description": "GNUID -> Calculator XGID -> Board",
                "xgid": converted_xgid,
                "factual_state": path_a_state,
                "render_type": (converted_record or {}).get("type"),
            },
            "path_b": {
                "description": "same complete GNUID -> Board directly",
                "factual_state": path_b_state,
                "render_type": direct_record.get("type"),
                "record": direct_record,
            },
            "classification": classification,
            "differences": differences,
        }
    except (ModuleNotFoundError, NotImplementedError) as exc:
        classification = RESULT_CLASSIFICATIONS[3]
    except Exception as exc:
        classification = RESULT_CLASSIFICATIONS[4]
    return {
        "consumer": "backgammonboard v0.1.1",
        "role": "renderer/consumer diagnostic; not conversion authority",
        "complete_gnuid": gnuid,
        "path_a": {"xgid": converted_xgid},
        "path_b": {},
        "classification": classification,
        "differences": [],
        "error": f"{type(exc).__name__}: {exc}",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def build_gallery(
    *,
    cases_path: Path,
    output_dir: Path,
    r_library: Path,
    case_id: str | None = None,
    calculator=None,
    bglab=None,
    gnu=None,
    renderer=None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    renders = output_dir / "renders"
    gnu_dir = output_dir / "gnu-cli"
    calculator = calculator or BackgammonCalculatorReference(r_library=r_library)
    bglab = bglab or BglabGnuidOracle(r_library=r_library)
    gnu = gnu or GnuBackgammonCli()
    renderer = renderer or BackgammonBoardRenderer()
    surfaces = (NativeSurface(), BridgeSurface(), AnkiSurface())

    cases_out: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    roundtrips: list[dict[str, Any]] = []
    case_html: list[str] = []

    for case in load_cases(cases_path, case_id):
        directions_html: list[str] = []
        for direction in (XGID_DIRECTION, GNUID_DIRECTION):
            xgid_to_gnuid = direction == XGID_DIRECTION
            source = case.xgid if xgid_to_gnuid else case.gnuid
            first_conversion = (
                calculator.xgid_to_gnuid(source)
                if xgid_to_gnuid
                else calculator.gnuid_to_xgid(source)
            )
            reference_middle = (
                first_conversion["gnuid"] if xgid_to_gnuid else first_conversion["xgid"]
            )
            return_conversion = (
                calculator.gnuid_to_xgid(reference_middle)
                if xgid_to_gnuid
                else calculator.xgid_to_gnuid(reference_middle)
            )
            reference_terminal = (
                return_conversion["xgid"] if xgid_to_gnuid else return_conversion["gnuid"]
            )

            gnu_post_import = (
                _gnu(gnu, source, gnu_dir, f"{case.case_id}-gnu-post-import")
                if xgid_to_gnuid
                else None
            )
            bglab_record = None
            if not xgid_to_gnuid:
                try:
                    bglab_record = bglab.convert(source)
                except Exception as exc:
                    bglab_record = {
                        "input": source,
                        "status": "unavailable",
                        "error": f"{type(exc).__name__}: {exc}",
                    }

            attempts = []
            for surface in surfaces:
                converter = (
                    surface.xgid_to_gnuid if xgid_to_gnuid else surface.gnuid_to_xgid
                )
                returner = (
                    surface.gnuid_to_xgid if xgid_to_gnuid else surface.xgid_to_gnuid
                )
                attempt = _attempt(
                    surface.name,
                    direction,
                    source,
                    reference_middle,
                    converter,
                    returner,
                )
                attempts.append(attempt)
                comparisons.append(
                    {
                        key: value
                        for key, value in attempt.items()
                        if key
                        not in {
                            "middle_diff_from_reference",
                            "roundtrip_diff_from_source",
                        }
                    }
                    | {"case_id": case.case_id}
                )
                roundtrips.append(
                    {
                        "case_id": case.case_id,
                        "direction": direction,
                        "surface": surface.name,
                        "source": source,
                        "middle": attempt.get("middle"),
                        "terminal": attempt.get("terminal"),
                        "exact": attempt["roundtrip_exact"],
                        "semantic": attempt["roundtrip_semantic"],
                        "classification": (
                            _classification(
                                attempt["roundtrip_exact"],
                                attempt["roundtrip_semantic"],
                            )
                            if attempt["status"] == "ok"
                            else attempt["classification"]
                        ),
                    }
                )

            identifiers = {source, reference_middle, reference_terminal} | {
                attempt[key]
                for attempt in attempts
                for key in ("middle", "terminal")
                if attempt.get(key)
            }
            render_cache: dict[str, Any] = {}
            gnu_cache: dict[str, Any] = {}
            canonical_cache: dict[str, Any] = {}
            calculator_cache: dict[str, Any] = {}
            for index, identifier in enumerate(sorted(identifiers)):
                key = f"{case.case_id}-{direction[0]}-{index}"
                render_cache[identifier] = _render(renderer, identifier, renders, key)
                gnu_cache[identifier] = (
                    _gnu(gnu, identifier, gnu_dir, key)
                    if not identifier.startswith("XGID=")
                    else None
                )
                canonical_cache[identifier] = _canonical_safe(identifier)
                calculator_cache[identifier] = _calculator_canonical(calculator, identifier)

            board_gnuid = reference_middle if xgid_to_gnuid else source
            board_xgid = reference_terminal if xgid_to_gnuid else reference_middle
            board_parity = _board_direct_gnuid_parity(
                renderer,
                board_gnuid,
                board_xgid,
                renders,
                f"{case.case_id}-{direction[0]}-board-parity",
                render_cache.get(board_xgid),
            )

            def visuals(first: str, middle: str, terminal: str) -> dict[str, Any]:
                return {
                    "source_render": render_cache.get(first),
                    "middle_render": render_cache.get(middle),
                    "terminal_render": render_cache.get(terminal),
                    "source_gnu": gnu_cache.get(first),
                    "middle_gnu": gnu_cache.get(middle),
                    "terminal_gnu": gnu_cache.get(terminal),
                    "source_canonical": canonical_cache.get(first),
                    "middle_canonical": canonical_cache.get(middle),
                    "terminal_canonical": canonical_cache.get(terminal),
                    "reference_middle_canonical": calculator_cache.get(reference_middle),
                }

            reference_visuals = visuals(source, reference_middle, reference_terminal)
            reference_visuals["calculator_source_canonical"] = calculator_cache.get(source)
            reference_visuals["calculator_middle_canonical"] = calculator_cache.get(
                reference_middle
            )
            reference_visuals["calculator_terminal_canonical"] = calculator_cache.get(
                reference_terminal
            )
            reference = reference_card(
                direction,
                source,
                reference_middle,
                reference_terminal,
                reference_visuals,
                bglab_record=bglab_record,
                gnu_post_import=gnu_post_import,
                board_consumer_parity=board_parity,
            )
            methods = "".join(
                method_card(
                    attempt,
                    visuals(
                        source,
                        attempt.get("middle") or source,
                        attempt.get("terminal") or source,
                    ),
                )
                for attempt in attempts
            )
            directions_html.append(
                f'<section class="direction"><h3>{e(direction)}</h3>{reference}'
                f'<div class="methods" data-column-order="{e(",".join(SURFACE_ORDER))}">'
                f"{methods}</div></section>"
            )
            cases_out.append(
                {
                    "case_id": case.case_id,
                    "label": case.label,
                    "direction": direction,
                    "source": source,
                    "reference_middle": reference_middle,
                    "reference_terminal": reference_terminal,
                    "calculator_conversion": first_conversion,
                    "calculator_return_conversion": return_conversion,
                    "calculator_canonical": {
                        "source": calculator_cache.get(source),
                        "converted": calculator_cache.get(reference_middle),
                        "roundtrip": calculator_cache.get(reference_terminal),
                    },
                    "gnu_post_import": gnu_post_import,
                    "bglab_diagnostic": bglab_record,
                    "board_direct_gnuid_consumer_parity": board_parity,
                    "methods": attempts,
                }
            )
        case_html.append(
            f'<section class="case"><h2>{e(case.case_id)} · {e(case.label)}</h2>'
            f'{"".join(directions_html)}</section>'
        )

    provenance = {
        "calculator": calculator.provenance,
        "bglab": {
            **bglab.provenance,
            "role": "secondary diagnostic only; not canonical",
        },
        "gnu": {
            **gnu.provenance,
            "role": "independent primary GNU behavior evidence",
        },
        "renderer": {
            **renderer.provenance,
            "role": "renderer/consumer; not canonical conversion authority",
        },
        "engine_kit": {"module": str(Path(_bek().__file__).resolve())},
        "ankigammon": {"version": getattr(surfaces[2], "version", "unknown")},
    }
    report = {
        "schema": "stable-player-oracle-first-gallery-v4",
        "classifications": list(RESULT_CLASSIFICATIONS),
        "selected_case_id": case_id,
        "cases": cases_out,
        "comparisons": comparisons,
        "roundtrips": roundtrips,
        "provenance": provenance,
    }
    # Evidence is always written before semantic failure is returned by main.
    (output_dir / "oracle-comparison-results.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_csv(output_dir / "method-comparisons.csv", comparisons)
    _write_csv(output_dir / "roundtrips.csv", roundtrips)
    (output_dir / "oracle-gallery.html").write_text(
        render_page(case_html, provenance), encoding="utf-8"
    )
    return report


def semantic_exit_code(report: dict[str, Any]) -> int:
    hard = [
        comparison
        for comparison in report["comparisons"]
        if comparison["surface"] in {"native_python", "engine_kit"}
        and not comparison["reference_semantic"]
    ]
    return 1 if hard else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--r-library", type=Path, required=True)
    args = parser.parse_args()
    report = build_gallery(
        cases_path=args.cases,
        case_id=args.case_id,
        output_dir=args.output,
        r_library=args.r_library,
    )
    return semantic_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
