from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .calculator_reference import (
    EXPECTED_VERSION,
    GITHUB_REPOSITORY,
    RELEASE_COMMIT,
    REQUESTED_RELEASE_REF,
)
from .engine import BridgePreparationUnavailable
from .models import RESULT_CLASSIFICATIONS
from .oracle_gallery import AnkiSurface, BridgeSurface, NativeSurface, _engine_factual


XGID_DIRECTION = "source XGID -> GNUID -> XGID"
GNUID_DIRECTION = "source GNUID -> XGID -> GNUID"
SURFACE_NAMES = (
    "calculator_v0_2_0",
    "engine_kit_native",
    "engine_kit_public",
    "ankigammon_direct",
)
DETAIL_FIELDS = (
    "input_row",
    "surface",
    "direction",
    "source_identifier",
    "paired_source_identifier",
    "calculator_reference_middle_identifier",
    "surface_middle_identifier",
    "surface_terminal_identifier",
    "primary_conversion_status",
    "primary_exact_vs_calculator",
    "primary_factual_vs_calculator",
    "primary_classification",
    "round_trip_status",
    "round_trip_exact_vs_source",
    "round_trip_factual_vs_source",
    "round_trip_classification",
    "primary_differences",
    "round_trip_differences",
    "error_or_unsupported_reason",
    "action",
    "crawford_status",
    "session_type",
)
FACT_FIELDS = (
    "player_0_points",
    "player_1_points",
    "player_0_bar",
    "player_1_bar",
    "player_0_off",
    "player_1_off",
    "on_roll",
    "decision_player",
    "action",
    "dice",
    "cube_value",
    "cube_owner",
    "score_player_0",
    "score_player_1",
    "match_length",
    "crawford",
    "jacoby",
    "beavers",
    "maximum_cube",
)


@dataclass(frozen=True)
class Surface:
    name: str
    xgid_to_gnuid: Callable[[str], str]
    gnuid_to_xgid: Callable[[str], str]
    public_bridge: bool = False


@dataclass
class RunOutcome:
    exit_code: int
    output_dir: Path
    summary: dict[str, Any]


class CalculatorBatchRunner:
    """One-process released-Calculator reference generation."""

    def __init__(
        self,
        *,
        rscript: Path | str | None = None,
        r_library: Path | str | None = None,
        script: Path | None = None,
    ) -> None:
        self.rscript = _discover_rscript(rscript)
        configured_library = r_library or os.environ.get(
            "BACKGAMMONCALCULATOR_R_LIBRARY", ""
        )
        self.r_library = (
            Path(configured_library).expanduser().resolve()
            if configured_library
            else None
        )
        self.script = script or (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "calculator_external_batch.R"
        )

    def run(
        self, input_csv: Path, output_dir: Path, progress_interval: int
    ) -> tuple[Path, dict[str, str]]:
        reference_path = output_dir / "calculator-reference.csv"
        provenance_path = output_dir / "calculator-provenance.csv"
        command = [
            str(self.rscript),
            "--vanilla",
            str(self.script),
            str(input_csv),
            str(reference_path),
            str(provenance_path),
            str(self.r_library or ""),
            str(progress_interval),
        ]
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                "Calculator batch failed with exit code {}: {}".format(
                    completed.returncode, " ".join(command)
                )
            )
        with provenance_path.open(newline="", encoding="utf-8-sig") as handle:
            provenance = next(csv.DictReader(handle))
        if provenance.get("package_version") != EXPECTED_VERSION:
            raise RuntimeError("Calculator batch returned unexpected package version")
        if provenance.get("resolved_release_commit") != RELEASE_COMMIT:
            raise RuntimeError("Calculator batch returned unexpected resolved commit")
        return reference_path, provenance


def _discover_rscript(explicit: Path | str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for variable in ("RSCRIPT", "R_SCRIPT"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]))
    found = shutil.which("Rscript") or shutil.which("Rscript.exe")
    if found:
        candidates.append(Path(found))
    for root in (Path("C:/Program Files/R"), Path("C:/Program Files (x86)/R")):
        if root.exists():
            candidates.extend(sorted(root.glob("R-*/bin/Rscript.exe"), reverse=True))
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    raise FileNotFoundError("Rscript not found. Set RSCRIPT or put Rscript on PATH.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_sha256s(directory: Path) -> Path:
    target = directory / "SHA256SUMS.txt"
    lines = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path != target:
            lines.append(f"{_sha256(path)}  {path.relative_to(directory).as_posix()}")
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def write_file_hash(path: Path) -> Path:
    target = Path(f"{path}.sha256")
    target.write_text(f"{_sha256(path)}  {path.name}\n", encoding="utf-8")
    return target


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def _read_input(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        missing = [name for name in ("gnuid", "xgid") if name not in fieldnames]
        if missing:
            raise ValueError(
                "input CSV missing required column(s): {}".format(", ".join(missing))
            )
        rows = [dict(row) for row in reader]
    return rows, fieldnames


def _bool(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


def _integer(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fact(row: Mapping[str, str], prefix: str) -> tuple[str, str, dict[str, Any] | None]:
    status = row.get(f"{prefix}status", "error")
    error = row.get(f"{prefix}error", "")
    if status != "ok":
        return status, error, None
    points = lambda name: [
        int(value)
        for value in row.get(f"{prefix}{name}", "").split(";")
        if value != ""
    ]
    owner = row.get(f"{prefix}cube_owner", "") or None
    if owner == "centered":
        owner = "center"
    value = {
        "stable_player_identity": {"player_0": "player_0", "player_1": "player_1"},
        "checker_points": {
            "player_0": points("player_0_points"),
            "player_1": points("player_1_points"),
        },
        "bars": {
            "player_0": _integer(row.get(f"{prefix}player_0_bar", "")),
            "player_1": _integer(row.get(f"{prefix}player_1_bar", "")),
        },
        "borne_off": {
            "player_0": _integer(row.get(f"{prefix}player_0_off", "")),
            "player_1": _integer(row.get(f"{prefix}player_1_off", "")),
        },
        "state": {
            "on_roll": row.get(f"{prefix}on_roll", "") or None,
            "decision_player": row.get(f"{prefix}decision_player", "") or None,
            "action": row.get(f"{prefix}action", "") or None,
            "dice": points("dice") or None,
        },
        "cube": {
            "value": _integer(row.get(f"{prefix}cube_value", "")),
            "owner": owner,
        },
        "score": {
            "player_0": _integer(row.get(f"{prefix}score_player_0", "")),
            "player_1": _integer(row.get(f"{prefix}score_player_1", "")),
            "match_length": _integer(row.get(f"{prefix}match_length", "")),
        },
        "rules": {
            "crawford": _bool(row.get(f"{prefix}crawford", "")),
            "jacoby": _bool(row.get(f"{prefix}jacoby", "")),
            "beavers": _bool(row.get(f"{prefix}beavers", "")),
            "maximum_cube": _integer(row.get(f"{prefix}maximum_cube", "")),
        },
    }
    return status, error, value


def _normalized_fact(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(value))
    rules = normalized.get("rules", {})
    rules.pop("maximum_cube", None)
    # These rule flags are absent in one identifier format when false. Treat
    # explicit false and unavailable/default null as the same factual state.
    for name in ("crawford", "jacoby", "beavers"):
        if rules.get(name) is not True:
            rules[name] = None
    return normalized


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    result: dict[str, Any] = {}
    for key, child in value.items():
        result.update(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    return result


def _compare_facts(
    expected: Mapping[str, Any] | None, actual: Mapping[str, Any] | None
) -> tuple[bool, list[dict[str, Any]]]:
    if expected is None or actual is None:
        return False, []
    left = _normalized_fact(expected)
    right = _normalized_fact(actual)
    left_flat, right_flat = _flatten(left), _flatten(right)
    differences = [
        {"path": path, "expected": left_flat.get(path), "actual": right_flat.get(path)}
        for path in sorted(set(left_flat) | set(right_flat))
        if left_flat.get(path) != right_flat.get(path)
    ]
    return not differences, differences


def _classification(exact: bool, factual: bool) -> str:
    if exact:
        return RESULT_CLASSIFICATIONS[0]
    if factual:
        return RESULT_CLASSIFICATIONS[1]
    return RESULT_CLASSIFICATIONS[2]


def _source_dimensions(row: Mapping[str, str]) -> tuple[str, str, str]:
    action = (row.get("action") or "unknown").strip() or "unknown"
    crawford_value = row.get("crawford")
    post_value = row.get("post_crawford")
    if _bool(str(crawford_value or "")) is True:
        crawford = "crawford"
    elif _bool(str(post_value or "")) is True:
        crawford = "post-crawford"
    elif crawford_value is None and post_value is None:
        crawford = "unknown"
    else:
        crawford = "neither"
    match_length = _integer(row.get("match_length", ""))
    session = "match" if match_length and match_length > 0 else "money"
    if match_length is None:
        xgid = row.get("xgid", "")
        try:
            match_length = int(xgid.split(":")[-2])
            session = "match" if match_length > 0 else "money"
        except (IndexError, ValueError):
            session = "unknown"
    return action, crawford, session


def _default_surfaces() -> list[Surface]:
    def failed(name: str, exc: Exception, *, public_bridge: bool = False) -> Surface:
        def raise_failure(value: str) -> str:
            if value:
                raise exc
            raise exc

        return Surface(name, raise_failure, raise_failure, public_bridge=public_bridge)

    try:
        native = NativeSurface()
        native_surface = Surface(
            "engine_kit_native", native.xgid_to_gnuid, native.gnuid_to_xgid
        )
    except Exception as exc:
        native_surface = failed("engine_kit_native", exc)
    try:
        public = BridgeSurface()
        public_surface = Surface(
            "engine_kit_public",
            public.xgid_to_gnuid,
            public.gnuid_to_xgid,
            public_bridge=True,
        )
    except Exception as exc:
        public_surface = failed("engine_kit_public", exc, public_bridge=True)
    try:
        anki = AnkiSurface()
        anki_surface = Surface(
            "ankigammon_direct", anki.xgid_to_gnuid, anki.gnuid_to_xgid
        )
    except Exception as exc:
        anki_surface = failed("ankigammon_direct", exc)
    return [
        native_surface,
        public_surface,
        anki_surface,
    ]


def _surface_attempt(
    surface: Surface,
    direction: str,
    source: str,
    paired_source: str,
    reference_middle: str,
    reference_fact: Mapping[str, Any] | None,
    source_fact: Mapping[str, Any] | None,
    dimensions: tuple[str, str, str],
    factual_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    convert, returner = (
        (surface.xgid_to_gnuid, surface.gnuid_to_xgid)
        if direction == XGID_DIRECTION
        else (surface.gnuid_to_xgid, surface.xgid_to_gnuid)
    )

    def decode(identifier: str) -> dict[str, Any]:
        if identifier not in factual_cache:
            factual_cache[identifier] = _engine_factual(identifier)
        return factual_cache[identifier]

    result = _empty_detail(surface.name, direction, source, paired_source, dimensions)
    result["calculator_reference_middle_identifier"] = reference_middle
    try:
        middle = convert(source)
    except BridgePreparationUnavailable as exc:
        if not surface.public_bridge:
            result["primary_conversion_status"] = "error"
            result["primary_classification"] = RESULT_CLASSIFICATIONS[4]
        else:
            result["primary_conversion_status"] = exc.status
            result["primary_classification"] = RESULT_CLASSIFICATIONS[3]
        result["round_trip_status"] = "not_attempted"
        result["round_trip_classification"] = result["primary_classification"]
        result["error_or_unsupported_reason"] = f"{type(exc).__name__}: {exc}"
        return result
    except (ModuleNotFoundError, NotImplementedError) as exc:
        category = RESULT_CLASSIFICATIONS[3] if surface.name == "ankigammon_direct" else RESULT_CLASSIFICATIONS[4]
        result["primary_conversion_status"] = "unavailable" if category == RESULT_CLASSIFICATIONS[3] else "error"
        result["primary_classification"] = category
        result["round_trip_status"] = "not_attempted"
        result["round_trip_classification"] = category
        result["error_or_unsupported_reason"] = f"{type(exc).__name__}: {exc}"
        return result
    except Exception as exc:
        result["primary_conversion_status"] = "error"
        result["primary_classification"] = RESULT_CLASSIFICATIONS[4]
        result["round_trip_status"] = "not_attempted"
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        result["error_or_unsupported_reason"] = f"{type(exc).__name__}: {exc}"
        return result

    result["surface_middle_identifier"] = middle
    result["primary_conversion_status"] = "ok"
    result["primary_exact_vs_calculator"] = middle == reference_middle
    if reference_fact is None:
        result["primary_classification"] = RESULT_CLASSIFICATIONS[4]
        result["error_or_unsupported_reason"] = "Calculator canonical middle state unavailable"
    else:
        try:
            factual, differences = _compare_facts(reference_fact, decode(middle))
            result["primary_factual_vs_calculator"] = factual
            result["primary_differences"] = json.dumps(differences, separators=(",", ":"))
            result["primary_classification"] = _classification(
                bool(result["primary_exact_vs_calculator"]), factual
            )
        except Exception as exc:
            result["primary_classification"] = RESULT_CLASSIFICATIONS[4]
            result["error_or_unsupported_reason"] = (
                f"factual decode: {type(exc).__name__}: {exc}"
            )

    try:
        terminal = returner(middle)
    except BridgePreparationUnavailable as exc:
        if surface.public_bridge:
            result["round_trip_status"] = exc.status
            result["round_trip_classification"] = RESULT_CLASSIFICATIONS[3]
        else:
            result["round_trip_status"] = "error"
            result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        reason = f"{type(exc).__name__}: {exc}"
        result["error_or_unsupported_reason"] = " | ".join(
            filter(None, (str(result["error_or_unsupported_reason"]), reason))
        )
        return result
    except (ModuleNotFoundError, NotImplementedError) as exc:
        category = RESULT_CLASSIFICATIONS[3] if surface.name == "ankigammon_direct" else RESULT_CLASSIFICATIONS[4]
        result["round_trip_status"] = "unavailable" if category == RESULT_CLASSIFICATIONS[3] else "error"
        result["round_trip_classification"] = category
        result["error_or_unsupported_reason"] = f"{type(exc).__name__}: {exc}"
        return result
    except Exception as exc:
        result["round_trip_status"] = "error"
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        result["error_or_unsupported_reason"] = f"{type(exc).__name__}: {exc}"
        return result

    result["surface_terminal_identifier"] = terminal
    result["round_trip_status"] = "ok"
    result["round_trip_exact_vs_source"] = terminal == source
    if source_fact is None:
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        reason = "Calculator canonical source state unavailable"
        result["error_or_unsupported_reason"] = " | ".join(
            filter(None, (str(result["error_or_unsupported_reason"]), reason))
        )
        return result
    try:
        factual, differences = _compare_facts(source_fact, decode(terminal))
        result["round_trip_factual_vs_source"] = factual
        result["round_trip_differences"] = json.dumps(differences, separators=(",", ":"))
        result["round_trip_classification"] = _classification(
            bool(result["round_trip_exact_vs_source"]), factual
        )
    except Exception as exc:
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        reason = f"round-trip factual decode: {type(exc).__name__}: {exc}"
        result["error_or_unsupported_reason"] = " | ".join(
            filter(None, (str(result["error_or_unsupported_reason"]), reason))
        )
    return result


def _empty_detail(
    surface: str,
    direction: str,
    source: str,
    paired_source: str,
    dimensions: tuple[str, str, str],
) -> dict[str, Any]:
    action, crawford, session = dimensions
    return {
        "input_row": "",
        "surface": surface,
        "direction": direction,
        "source_identifier": source,
        "paired_source_identifier": paired_source,
        "calculator_reference_middle_identifier": "",
        "surface_middle_identifier": "",
        "surface_terminal_identifier": "",
        "primary_conversion_status": "not_attempted",
        "primary_exact_vs_calculator": False,
        "primary_factual_vs_calculator": False,
        "primary_classification": RESULT_CLASSIFICATIONS[4],
        "round_trip_status": "not_attempted",
        "round_trip_exact_vs_source": False,
        "round_trip_factual_vs_source": False,
        "round_trip_classification": RESULT_CLASSIFICATIONS[4],
        "primary_differences": "[]",
        "round_trip_differences": "[]",
        "error_or_unsupported_reason": "",
        "action": action,
        "crawford_status": crawford,
        "session_type": session,
    }


def _calculator_attempt(
    reference: Mapping[str, str],
    direction: str,
    source: str,
    paired_source: str,
    source_fact: Mapping[str, Any] | None,
    dimensions: tuple[str, str, str],
) -> dict[str, Any]:
    prefix = "x" if direction == XGID_DIRECTION else "g"
    result = _empty_detail(
        "calculator_v0_2_0", direction, source, paired_source, dimensions
    )
    primary_status = reference.get(f"{prefix}_primary_status", "error")
    result["primary_conversion_status"] = primary_status
    result["calculator_reference_middle_identifier"] = reference.get(
        f"{prefix}_primary_middle", ""
    )
    result["surface_middle_identifier"] = result[
        "calculator_reference_middle_identifier"
    ]
    if primary_status != "ok":
        result["primary_classification"] = RESULT_CLASSIFICATIONS[4]
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        result["error_or_unsupported_reason"] = reference.get(
            f"{prefix}_primary_error", "Calculator primary conversion failed"
        )
        return result
    result["primary_exact_vs_calculator"] = True
    result["primary_factual_vs_calculator"] = True
    result["primary_classification"] = RESULT_CLASSIFICATIONS[0]
    if _fact(reference, f"{prefix}_mid__")[2] is None:
        result["primary_factual_vs_calculator"] = False
        result["primary_classification"] = RESULT_CLASSIFICATIONS[4]
        result["error_or_unsupported_reason"] = reference.get(
            f"{prefix}_mid__error", "Calculator could not decode its middle identifier"
        )

    round_status = reference.get(f"{prefix}_roundtrip_status", "error")
    result["round_trip_status"] = round_status
    terminal = reference.get(f"{prefix}_roundtrip_terminal", "")
    result["surface_terminal_identifier"] = terminal
    if round_status != "ok":
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        result["error_or_unsupported_reason"] = reference.get(
            f"{prefix}_roundtrip_error", "Calculator return conversion failed"
        )
        return result
    terminal_fact = _fact(reference, f"{prefix}_terminal__")[2]
    if source_fact is None or terminal_fact is None:
        result["round_trip_classification"] = RESULT_CLASSIFICATIONS[4]
        reason = reference.get(
            f"{prefix}_terminal__error", "Calculator canonical comparison unavailable"
        )
        result["error_or_unsupported_reason"] = " | ".join(
            filter(None, (str(result["error_or_unsupported_reason"]), reason))
        )
        return result
    factual, differences = _compare_facts(source_fact, terminal_fact)
    result["round_trip_exact_vs_source"] = terminal == source
    result["round_trip_factual_vs_source"] = factual
    result["round_trip_differences"] = json.dumps(differences, separators=(",", ":"))
    result["round_trip_classification"] = _classification(
        bool(result["round_trip_exact_vs_source"]), factual
    )
    return result


def _hard_failure(detail: Mapping[str, Any]) -> bool:
    surface = detail["surface"]
    primary = detail["primary_classification"]
    roundtrip = detail["round_trip_classification"]
    if surface == "calculator_v0_2_0":
        return primary == RESULT_CLASSIFICATIONS[4] or roundtrip == RESULT_CLASSIFICATIONS[4]
    if surface in {"engine_kit_native", "engine_kit_public"}:
        if primary in {RESULT_CLASSIFICATIONS[2], RESULT_CLASSIFICATIONS[4]}:
            return True
        if detail["round_trip_status"] == "ok" and roundtrip == RESULT_CLASSIFICATIONS[2]:
            return True
        if roundtrip == RESULT_CLASSIFICATIONS[4]:
            return True
    return False


def _json_counter(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter, key=str)}


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_metadata(path: Path, metadata: Mapping[str, Any]) -> None:
    lines = []
    for key, value in metadata.items():
        serialized = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        lines.append(f"{key}: {serialized}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _anki_provenance() -> dict[str, Any]:
    try:
        module = importlib.import_module("ankigammon")
        version = importlib.metadata.version("ankigammon")
        distribution = importlib.metadata.distribution("ankigammon")
        direct_url = distribution.read_text("direct_url.json")
        return {
            "version": version,
            "module": str(Path(module.__file__).resolve()),
            "direct_url": json.loads(direct_url) if direct_url else None,
        }
    except Exception as exc:
        return {"version": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def _summary_html(summary: Mapping[str, Any]) -> str:
    counts = summary.get("counts", {})
    rows = "".join(
        "<tr><th>{}</th><td>{}</td></tr>".format(html.escape(str(key)), html.escape(str(value)))
        for key, value in summary.get("headline", {}).items()
    )
    sections = "".join(
        "<h2>{}</h2><pre>{}</pre>".format(
            html.escape(str(name)), html.escape(json.dumps(values, indent=2, sort_keys=True))
        )
        for name, values in counts.items()
    )
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>External identifier batch</title>
<style>body{{font:15px system-ui;margin:2rem;max-width:1100px}}table{{border-collapse:collapse}}th,td{{padding:.4rem .7rem;border:1px solid #bbb;text-align:left}}pre{{background:#f5f5f5;padding:1rem;overflow:auto}}</style>
</head><body><h1>External identifier batch validation</h1><table>{rows}</table>{sections}</body></html>"""


def _write_empty_outputs(output_dir: Path) -> None:
    _write_csv(output_dir / "roundtrip-results.csv", DETAIL_FIELDS, [])
    _write_csv(output_dir / "mismatches.csv", DETAIL_FIELDS, [])
    _write_csv(output_dir / "unsupported.csv", DETAIL_FIELDS, [])
    _write_csv(output_dir / "errors.csv", DETAIL_FIELDS, [])
    _write_csv(
        output_dir / "source-pair-diagnostics.csv",
        (
            "input_row", "source_xgid", "source_gnuid", "xgid_to_supplied_gnuid_exact",
            "gnuid_to_supplied_xgid_exact", "factual_equivalent", "classification",
            "field_differences", "error", "action", "crawford_status", "session_type",
        ),
        [],
    )


def run_external_batch(
    input_csv: Path,
    output_dir: Path,
    *,
    calculator_runner: Any | None = None,
    surfaces: Sequence[Surface] | None = None,
    progress_interval: int = 1000,
    repo: Path | None = None,
) -> RunOutcome:
    input_csv = input_csv.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    repo = (repo or Path(__file__).resolve().parents[4]).resolve()
    started = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, Any] = {
        "started_utc": started,
        "source_csv_absolute_path": str(input_csv),
        "source_csv_sha256": _sha256(input_csv) if input_csv.is_file() else "unavailable",
        "engine_kit_branch": _git(repo, "branch", "--show-current"),
        "engine_kit_commit": _git(repo, "rev-parse", "HEAD"),
        "calculator_requested_release": REQUESTED_RELEASE_REF,
        "calculator_expected_commit": RELEASE_COMMIT,
        "python_executable": sys.executable,
        "rscript_executable": "pending",
        "ankigammon": _anki_provenance(),
        "classification_vocabulary": list(RESULT_CLASSIFICATIONS),
    }
    _write_metadata(output_dir / "RUN-METADATA.txt", metadata)
    _write_empty_outputs(output_dir)

    fatal_error = ""
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    reference_path: Path | None = None
    calculator_provenance: dict[str, str] = {}
    try:
        rows, fieldnames = _read_input(input_csv)
        metadata["source_row_count"] = len(rows)
        metadata["source_columns"] = fieldnames
        runner = calculator_runner or CalculatorBatchRunner()
        reference_path, calculator_provenance = runner.run(
            input_csv, output_dir, progress_interval
        )
        metadata["calculator_resolved_commit"] = calculator_provenance.get(
            "resolved_release_commit", "unknown"
        )
        metadata["calculator_provenance"] = calculator_provenance
        metadata["rscript_executable"] = calculator_provenance.get("rscript", "unknown")
    except Exception as exc:
        fatal_error = f"{type(exc).__name__}: {exc}"

    counters: dict[str, Counter[Any]] = {
        name: Counter()
        for name in (
            "surface", "direction", "primary_classification", "round_trip_classification",
            "action", "crawford_status", "session_type", "surface_direction_primary",
            "surface_direction_round_trip",
        )
    }
    headline: dict[str, Any] = {
        "total_source_rows": len(rows),
        "unique_gnuids": len({row.get("gnuid", "") for row in rows}),
        "unique_xgids": len({row.get("xgid", "") for row in rows}),
        "unique_pairs": len({(row.get("gnuid", ""), row.get("xgid", "")) for row in rows}),
        "total_detailed_chain_rows": 0,
        "source_pair_factual_matches": 0,
        "source_pair_factual_mismatches": 0,
        "source_pair_errors": 0,
        "engine_kit_native_hard_failures": 0,
        "engine_kit_public_hard_failures": 0,
        "public_bridge_unsupported_unavailable": 0,
        "calculator_errors": 0,
        "direct_ankigammon_factual_mismatches": 0,
        "exact_round_trips": 0,
        "semantic_round_trips": 0,
        "hard_failures": 0,
    }

    if not fatal_error and reference_path is not None:
        pair_fields = (
            "input_row", "source_xgid", "source_gnuid", "xgid_to_supplied_gnuid_exact",
            "gnuid_to_supplied_xgid_exact", "factual_equivalent", "classification",
            "field_differences", "error", "action", "crawford_status", "session_type",
        )
        with (
            reference_path.open(newline="", encoding="utf-8-sig") as ref_handle,
            (output_dir / "roundtrip-results.csv").open("w", newline="", encoding="utf-8") as detail_handle,
            (output_dir / "mismatches.csv").open("w", newline="", encoding="utf-8") as mismatch_handle,
            (output_dir / "unsupported.csv").open("w", newline="", encoding="utf-8") as unsupported_handle,
            (output_dir / "errors.csv").open("w", newline="", encoding="utf-8") as error_handle,
            (output_dir / "source-pair-diagnostics.csv").open("w", newline="", encoding="utf-8") as pair_handle,
        ):
            ref_reader = csv.DictReader(ref_handle)
            detail_writer = csv.DictWriter(detail_handle, fieldnames=DETAIL_FIELDS)
            mismatch_writer = csv.DictWriter(mismatch_handle, fieldnames=DETAIL_FIELDS)
            unsupported_writer = csv.DictWriter(unsupported_handle, fieldnames=DETAIL_FIELDS)
            error_writer = csv.DictWriter(error_handle, fieldnames=DETAIL_FIELDS)
            pair_writer = csv.DictWriter(pair_handle, fieldnames=pair_fields)
            for writer in (detail_writer, mismatch_writer, unsupported_writer, error_writer, pair_writer):
                writer.writeheader()

            try:
                active_surfaces = list(surfaces) if surfaces is not None else _default_surfaces()
                for index, (source_row, reference) in enumerate(zip(rows, ref_reader), 1):
                    if int(reference.get("input_row", "0")) != index:
                        raise RuntimeError(f"Calculator reference row order mismatch at input row {index}")
                    dimensions = _source_dimensions(source_row)
                    xgid, gnuid = source_row["xgid"], source_row["gnuid"]
                    sx_status, sx_error, sx_fact = _fact(reference, "src_x__")
                    sg_status, sg_error, sg_fact = _fact(reference, "src_g__")
                    pair_factual, pair_diff = _compare_facts(sx_fact, sg_fact)
                    pair_error = " | ".join(filter(None, (sx_error, sg_error)))
                    if sx_status != "ok" or sg_status != "ok":
                        pair_classification = RESULT_CLASSIFICATIONS[4]
                        headline["source_pair_errors"] += 1
                    elif pair_factual:
                        pair_classification = (
                            RESULT_CLASSIFICATIONS[0]
                            if reference.get("x_primary_middle") == gnuid
                            and reference.get("g_primary_middle") == xgid
                            else RESULT_CLASSIFICATIONS[1]
                        )
                        headline["source_pair_factual_matches"] += 1
                    else:
                        pair_classification = RESULT_CLASSIFICATIONS[2]
                        headline["source_pair_factual_mismatches"] += 1
                    pair_writer.writerow({
                        "input_row": index,
                        "source_xgid": xgid,
                        "source_gnuid": gnuid,
                        "xgid_to_supplied_gnuid_exact": reference.get("x_primary_middle") == gnuid,
                        "gnuid_to_supplied_xgid_exact": reference.get("g_primary_middle") == xgid,
                        "factual_equivalent": pair_factual,
                        "classification": pair_classification,
                        "field_differences": json.dumps(pair_diff, separators=(",", ":")),
                        "error": pair_error,
                        "action": dimensions[0], "crawford_status": dimensions[1], "session_type": dimensions[2],
                    })

                    factual_cache: dict[str, dict[str, Any]] = {}
                    for direction in (XGID_DIRECTION, GNUID_DIRECTION):
                        prefix = "x" if direction == XGID_DIRECTION else "g"
                        source = xgid if prefix == "x" else gnuid
                        paired = gnuid if prefix == "x" else xgid
                        source_fact = sx_fact if prefix == "x" else sg_fact
                        reference_middle = reference.get(f"{prefix}_primary_middle", "")
                        reference_fact = _fact(reference, f"{prefix}_mid__")[2]
                        details = [
                            _calculator_attempt(reference, direction, source, paired, source_fact, dimensions)
                        ]
                        for surface in active_surfaces:
                            details.append(_surface_attempt(
                                surface, direction, source, paired, reference_middle,
                                reference_fact, source_fact, dimensions, factual_cache,
                            ))
                        for detail in details:
                            detail["input_row"] = index
                            detail_writer.writerow(detail)
                            headline["total_detailed_chain_rows"] += 1
                            for key in ("surface", "direction", "primary_classification", "round_trip_classification", "action", "crawford_status", "session_type"):
                                counters[key][detail[key]] += 1
                            counters["surface_direction_primary"][(detail["surface"], detail["direction"], detail["primary_classification"])] += 1
                            counters["surface_direction_round_trip"][(detail["surface"], detail["direction"], detail["round_trip_classification"])] += 1
                            if detail["primary_classification"] == RESULT_CLASSIFICATIONS[2] or detail["round_trip_classification"] == RESULT_CLASSIFICATIONS[2]:
                                mismatch_writer.writerow(detail)
                            if RESULT_CLASSIFICATIONS[3] in (detail["primary_classification"], detail["round_trip_classification"]):
                                unsupported_writer.writerow(detail)
                            if RESULT_CLASSIFICATIONS[4] in (detail["primary_classification"], detail["round_trip_classification"]):
                                error_writer.writerow(detail)
                            if detail["round_trip_exact_vs_source"]:
                                headline["exact_round_trips"] += 1
                            if detail["round_trip_factual_vs_source"]:
                                headline["semantic_round_trips"] += 1
                            hard = _hard_failure(detail)
                            if hard:
                                headline["hard_failures"] += 1
                                if detail["surface"] == "engine_kit_native":
                                    headline["engine_kit_native_hard_failures"] += 1
                                elif detail["surface"] == "engine_kit_public":
                                    headline["engine_kit_public_hard_failures"] += 1
                            if detail["surface"] == "engine_kit_public" and RESULT_CLASSIFICATIONS[3] in (detail["primary_classification"], detail["round_trip_classification"]):
                                headline["public_bridge_unsupported_unavailable"] += 1
                            if detail["surface"] == "calculator_v0_2_0" and RESULT_CLASSIFICATIONS[4] in (detail["primary_classification"], detail["round_trip_classification"]):
                                headline["calculator_errors"] += 1
                            if detail["surface"] == "ankigammon_direct" and RESULT_CLASSIFICATIONS[2] in (detail["primary_classification"], detail["round_trip_classification"]):
                                headline["direct_ankigammon_factual_mismatches"] += 1
                    if progress_interval > 0 and (index % progress_interval == 0 or index == len(rows)):
                        print(f"Engine Kit surfaces: {index}/{len(rows)} rows", flush=True)
                if next(ref_reader, None) is not None:
                    raise RuntimeError("Calculator reference contains more rows than the source CSV")
                if headline["total_detailed_chain_rows"] != len(rows) * 2 * len(SURFACE_NAMES):
                    raise RuntimeError("detail-row count does not equal input rows × directions × surfaces")
            except Exception as exc:
                fatal_error = f"{type(exc).__name__}: {exc}"

    if fatal_error:
        headline["hard_failures"] += 1
        headline["fatal_error"] = fatal_error

    summary = {
        "classifications": list(RESULT_CLASSIFICATIONS),
        "headline": headline,
        "counts": {name: _json_counter(counter) for name, counter in counters.items()},
        "provenance": {
            "source_csv": {"path": str(input_csv), "sha256": metadata["source_csv_sha256"]},
            "engine_kit": {"branch": metadata["engine_kit_branch"], "commit": metadata["engine_kit_commit"]},
            "calculator": calculator_provenance or {
                "package_version": EXPECTED_VERSION,
                "github_repository": GITHUB_REPOSITORY,
                "requested_release_ref": REQUESTED_RELEASE_REF,
                "resolved_release_commit": RELEASE_COMMIT,
            },
            "python_executable": sys.executable,
            "ankigammon": metadata["ankigammon"],
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary_rows: list[dict[str, Any]] = []
    for key, value in headline.items():
        summary_rows.append({"dimension": "headline", "key": key, "count": value})
    for dimension, counter in counters.items():
        for key, value in sorted(counter.items(), key=lambda item: str(item[0])):
            rendered_key = " | ".join(key) if isinstance(key, tuple) else str(key)
            summary_rows.append({"dimension": dimension, "key": rendered_key, "count": value})
    _write_csv(output_dir / "summary.csv", ("dimension", "key", "count"), summary_rows)
    (output_dir / "summary.html").write_text(_summary_html(summary), encoding="utf-8")
    metadata["completed_utc"] = datetime.now(timezone.utc).isoformat()
    metadata["validation_exit_code"] = 1 if headline["hard_failures"] else 0
    metadata["source_row_count"] = len(rows)
    metadata["calculator_resolved_commit"] = calculator_provenance.get("resolved_release_commit", "unavailable")
    if fatal_error:
        metadata["fatal_error"] = fatal_error
    _write_metadata(output_dir / "RUN-METADATA.txt", metadata)
    write_sha256s(output_dir)
    return RunOutcome(1 if headline["hard_failures"] else 0, output_dir, summary)


def _default_output(repo: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return repo / "artifacts" / f"external-identifier-batch-{timestamp}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch-validate external GNUID/XGID pairs")
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--r-library", type=Path)
    parser.add_argument("--rscript", type=Path)
    parser.add_argument("--progress-interval", type=int, default=1000)
    parser.add_argument("--rehash-evidence", type=Path)
    parser.add_argument("--hash-file", type=Path)
    args = parser.parse_args(argv)
    if args.rehash_evidence:
        write_sha256s(args.rehash_evidence.resolve())
        return 0
    if args.hash_file:
        hash_path = write_file_hash(args.hash_file.resolve())
        print(f"{_sha256(args.hash_file.resolve())}  {hash_path}")
        return 0
    if args.input is None:
        parser.error("input CSV is required")
    repo = Path(__file__).resolve().parents[4]
    output = args.output or _default_output(repo)
    runner = CalculatorBatchRunner(rscript=args.rscript, r_library=args.r_library)
    outcome = run_external_batch(
        args.input, output, calculator_runner=runner,
        progress_interval=args.progress_interval, repo=repo,
    )
    print(f"Evidence directory: {outcome.output_dir}")
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
