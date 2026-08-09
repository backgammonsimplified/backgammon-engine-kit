from __future__ import annotations

import html
import json
from typing import Any


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    out: dict[str, Any] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        out.update(_flatten(child, path))
    return out


def board_visual(
    identifier: str,
    renderer_record: dict[str, Any] | None,
    label: str,
) -> str:
    if not identifier or not identifier.startswith("XGID="):
        return (
            f'<section class="board-card unavailable"><h5>{e(label)}</h5>'
            '<p>Expected an XGID for backgammonboard rendering.</p></section>'
        )
    if renderer_record and renderer_record.get("output"):
        body = f'<div class="svg-wrap">{renderer_record["output"]}</div>'
    else:
        body = f'<pre>{e((renderer_record or {}).get("stderr") or "Renderer unavailable")}</pre>'
    return (
        f'<section class="board-card"><h5>{e(label)}</h5>'
        f'<code class="identifier">{e(identifier)}</code>{body}</section>'
    )


def gnu_visual(
    identifier: str,
    gnu_record: dict[str, Any] | None,
    label: str,
) -> str:
    if not identifier or identifier.startswith("XGID="):
        return (
            f'<section class="board-card unavailable"><h5>{e(label)}</h5>'
            '<p>Expected a GNUID for GNU CLI rendering.</p></section>'
        )
    board = (gnu_record or {}).get("board") or "GNU CLI board unavailable"
    return (
        f'<section class="board-card cli"><h5>{e(label)}</h5>'
        f'<code class="identifier">{e(identifier)}</code>'
        f'<pre>{e(board)}</pre></section>'
    )


def visual(
    identifier: str,
    renderer_record: dict[str, Any] | None,
    gnu_record: dict[str, Any] | None,
    label: str,
) -> str:
    if identifier.startswith("XGID="):
        return board_visual(identifier, renderer_record, label)
    return gnu_visual(identifier, gnu_record, label)


def canonical_compare(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    left_label: str,
    right_label: str,
    title: str,
    *,
    open_by_default: bool = True,
) -> str:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return (
            f'<section class="canonical-card"><h5>{e(title)}</h5>'
            '<p class="error-text">Canonical comparison unavailable.</p></section>'
        )
    left_flat = _flatten(left)
    right_flat = _flatten(right)
    paths = sorted(set(left_flat) | set(right_flat))
    differences = sum(left_flat.get(path) != right_flat.get(path) for path in paths)
    status = "exact" if differences == 0 else f"{differences} difference(s)"
    rows = []
    for path in paths:
        left_value = left_flat.get(path)
        right_value = right_flat.get(path)
        diff_class = " diff" if left_value != right_value else ""
        rows.append(
            '<div class="json-row">'
            f'<div class="json-cell{diff_class}"><pre>{e(json.dumps(path))}: {e(json.dumps(left_value, ensure_ascii=False))}</pre></div>'
            f'<div class="json-cell{diff_class}"><pre>{e(json.dumps(path))}: {e(json.dumps(right_value, ensure_ascii=False))}</pre></div>'
            '</div>'
        )
    open_attr = " open" if open_by_default else ""
    return (
        f'<section class="canonical-card"><h5>{e(title)}</h5>'
        f'<div class="comparison-line">Canonical comparison: <strong>{e(status)}</strong></div>'
        f'<details{open_attr}><summary>Canonical representation</summary>'
        '<div class="json-compare">'
        f'<div class="json-head"><div>{e(left_label)}</div><div>{e(right_label)}</div></div>'
        f'{"".join(rows)}</div></details></section>'
    )


def canonical_triplet(
    source: dict[str, Any] | None,
    converted: dict[str, Any] | None,
    roundtrip: dict[str, Any] | None,
) -> str:
    if not all(isinstance(value, dict) for value in (source, converted, roundtrip)):
        return (
            '<section class="canonical-card reference-canonical"><h5>'
            "Calculator canonical source / converted / round-trip comparison</h5>"
            '<p class="error-text">Calculator canonical comparison unavailable.</p></section>'
        )
    values = [source, converted, roundtrip]
    flattened = [_flatten(value) for value in values]
    paths = sorted(set().union(*(set(value) for value in flattened)))
    differences = [
        path
        for path in paths
        if len({json.dumps(value.get(path), sort_keys=True) for value in flattened}) > 1
    ]
    hard_differences = [path for path in differences if path != "rules.maximum_cube"]
    if not differences:
        classification = "exact agreement"
    elif not hard_differences:
        classification = "representational/default/normalization difference"
    else:
        classification = "factual state mismatch"
    rows = []
    for path in paths:
        cells = []
        for value in flattened:
            css = " diff" if path in differences else ""
            cells.append(
                f'<div class="json-cell{css}"><pre>{e(json.dumps(path))}: '
                f'{e(json.dumps(value.get(path), ensure_ascii=False))}</pre></div>'
            )
        rows.append(f'<div class="json-row triplet">{"".join(cells)}</div>')
    return (
        '<section class="canonical-card reference-canonical">'
        '<h5>Calculator canonical source / converted / round-trip factual comparison</h5>'
        f'<div class="comparison-line">Classification: <strong>{e(classification)}</strong></div>'
        '<details open><summary>Field-level Calculator canonical state</summary>'
        '<div class="json-compare"><div class="json-head triplet">'
        '<div>Source</div><div>Converted</div><div>Round trip</div></div>'
        f'{"".join(rows)}</div></details></section>'
    )


def diff_table(rows: list[dict[str, Any]], title: str) -> str:
    if not rows:
        return (
            f'<details><summary>{e(title)}: no differences</summary>'
            '<p class="pass-text">Canonical states align.</p></details>'
        )
    body = "".join(
        f'<tr><td><code>{e(r["path"])}</code></td>'
        f'<td>{e(json.dumps(r["left"], ensure_ascii=False))}</td>'
        f'<td class="diff">{e(json.dumps(r["right"], ensure_ascii=False))}</td></tr>'
        for r in rows
    )
    return (
        f'<details><summary>{e(title)}: {len(rows)} difference(s)</summary>'
        '<table class="diff-table"><tr><th>Path</th><th>Source</th><th>Round trip</th></tr>'
        f'{body}</table></details>'
    )


def method_card(attempt: dict[str, Any], visuals: dict[str, Any]) -> str:
    cls = "exact" if attempt["reference_exact"] else (
        "semantic_exact" if attempt["reference_semantic"] else "state_mismatch"
    )
    if attempt["roundtrip_status"] == "not_attempted":
        rt = "state_mismatch"
        rt_label = "not attempted"
    elif attempt["roundtrip_status"] != "ok":
        rt = "state_mismatch"
        rt_label = attempt["roundtrip_classification"]
    else:
        rt = "exact" if attempt["roundtrip_exact"] else (
            "semantic_exact" if attempt["roundtrip_semantic"] else "state_mismatch"
        )
        rt_label = "exact" if attempt["roundtrip_exact"] else (
            "semantic" if attempt["roundtrip_semantic"] else "changed"
        )
    badges = (
        f'<span class="badge {cls}">{e(attempt["classification"])}</span>'
        f'<span class="badge {rt}">round trip: {rt_label}</span>'
    )
    if attempt["status"] != "ok":
        return (
            f'<article class="method-card fail-card"><h4>{e(attempt["label"])}</h4>'
            f'<div class="status-row">{badges}</div><pre>{e(attempt["error"])}</pre></article>'
        )

    xgid_to_gnuid = attempt["direction"].startswith("XGID")
    if xgid_to_gnuid:
        gnu_id = attempt["middle"]
        gnu_record = visuals["middle_gnu"]
        gnu_label = "GNU CLI render of method GNUID"
        board_id = attempt["terminal"]
        board_record = visuals["terminal_render"]
        board_label = "backgammonboard round-trip XGID"
    else:
        gnu_id = attempt["terminal"]
        gnu_record = visuals["terminal_gnu"]
        gnu_label = "GNU CLI round-trip GNUID"
        board_id = attempt["middle"]
        board_record = visuals["middle_render"]
        board_label = "backgammonboard method XGID"

    canonical = canonical_compare(
        visuals.get("reference_middle_canonical"),
        visuals.get("middle_canonical"),
        "Calculator reference",
        attempt["label"],
        "Canonical representation",
        open_by_default=True,
    )
    roundtrip = diff_table(
        attempt["roundtrip_diff_from_source"],
        "Round-trip canonical state vs source",
    )
    roundtrip_notice = (
        f'<pre>{e(attempt["roundtrip_error"])}</pre>'
        if attempt["roundtrip_status"] != "ok"
        else ""
    )
    return (
        f'<article class="method-card"><h4>{e(attempt["label"])}</h4>'
        f'<div class="status-row">{badges}</div>'
        f'{gnu_visual(gnu_id, gnu_record, gnu_label)}'
        f'{board_visual(board_id, board_record, board_label)}'
        f'{canonical}{roundtrip_notice}{roundtrip}</article>'
    )


def reference_card(
    direction: str,
    source: str,
    middle: str,
    terminal: str,
    visuals: dict[str, Any],
    bglab_record: dict[str, Any] | None = None,
    gnu_post_import: dict[str, Any] | None = None,
    board_consumer_parity: dict[str, Any] | None = None,
) -> str:
    diagnostics = []
    if gnu_post_import:
        diagnostics.append(
            '<details><summary>GNU post-import diagnostic from source XGID</summary>'
            f'<p><strong>GNU result:</strong> <code>{e(gnu_post_import.get("complete_gnuid"))}</code></p>'
            f'<pre>{e(json.dumps(gnu_post_import, indent=2, ensure_ascii=False))}</pre></details>'
        )
    if bglab_record:
        diagnostics.append(
            '<details><summary>Diagnostic only: R bglab (not canonical)</summary>'
            f'<pre>{e(json.dumps(bglab_record, indent=2, ensure_ascii=False))}</pre></details>'
        )
    parity = board_consumer_parity or {}
    parity_html = (
        '<section class="board-parity"><h5>Board direct-complete-GNUID consumer parity</h5>'
        f'<span class="badge">{e(parity.get("classification", "unsupported/unavailable"))}</span>'
        '<p>Path A: complete GNUID → Calculator XGID → Board. '
        'Path B: the same complete GNUID → Board directly. Board is a consumer, not the conversion authority.</p>'
        '<details><summary>Board consumer factual states and differences</summary>'
        f'<pre>{e(json.dumps(parity, indent=2, ensure_ascii=False))}</pre></details></section>'
    )
    canonical = canonical_triplet(
        visuals.get("calculator_source_canonical"),
        visuals.get("calculator_middle_canonical"),
        visuals.get("calculator_terminal_canonical"),
    )
    return (
        '<article class="reference-card"><h4>Reference: backgammoncalculator 0.2.0</h4>'
        f'<p>{e(direction)}</p><div class="reference-lane">'
        f'{visual(source, visuals["source_render"], visuals["source_gnu"], "Source")}'
        f'{visual(middle, visuals["middle_render"], visuals["middle_gnu"], "Converted reference")}'
        f'{visual(terminal, visuals["terminal_render"], visuals["terminal_gnu"], "Reference round trip")}'
        f'</div>{canonical}{parity_html}<div class="diagnostics"><h5>Secondary diagnostics</h5>'
        f'{"".join(diagnostics)}</div></article>'
    )


CSS = r'''
:root {
  font-family: system-ui, "Segoe UI", Arial, sans-serif;
  color: #111b35;
  background: #f4f1eb;
  --bs-navy: #111b35;
  --bs-cream: #f8eedd;
  --bs-tan: #d8c5a5;
  --line: #d7dee6;
  --green: #245c3d;
  --green-bg: #e7f5eb;
  --amber: #7a5200;
  --amber-bg: #fff0c7;
  --red: #b42318;
  --red-bg: #ffebe9;
}
* { box-sizing: border-box; }
body { max-width: 2100px; margin: 0 auto; padding: 24px; }
.top { background: var(--bs-navy); color: var(--bs-cream); padding: 24px 28px; border-radius: 12px; }
.top h1 { margin: 0 0 8px; }
.top p { max-width: 1400px; margin-bottom: 0; }
.provenance { margin-top: 10px; padding: 10px 14px; background: #fff; border: 1px solid var(--bs-tan); border-radius: 8px; font-size: 12px; overflow-wrap: anywhere; }
.case { background: #fff; border: 1px solid #d9dfe5; border-radius: 12px; padding: 22px; margin: 24px 0; }
.case > h2 { margin-top: 0; border-bottom: 3px solid var(--bs-navy); padding-bottom: 8px; }
.direction { border-top: 3px solid var(--bs-tan); margin-top: 28px; padding-top: 18px; }
.reference-card { border: 2px solid var(--bs-tan); background: #fcf8f1; border-radius: 9px; padding: 12px; margin: 12px 0 18px; }
.reference-card h4, .method-card h4 { margin-top: 0; }
.reference-lane { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: start; }
.methods { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; align-items: start; }
.method-card { min-width: 0; border: 1px solid #ccd5df; border-radius: 9px; padding: 12px; background: #fbfcfd; }
.identifier { display: block; overflow-wrap: anywhere; word-break: break-word; margin: 7px 0; font: 11px/1.35 Consolas, "Courier New", monospace; color: #111b35; }
.board-card { border: 1px solid #d8dfe6; border-radius: 7px; padding: 8px; margin: 10px 0; background: #fff; }
.board-card h5, .canonical-card h5 { margin: 0 0 7px; }
.svg-wrap { height: 285px; overflow: auto; border: 1px solid #e1e5ea; background: #fff; }
.svg-wrap svg { width: 100%; height: auto; max-height: 275px; }
.board-card.cli pre { margin: 0; min-height: 270px; max-height: 330px; overflow: auto; white-space: pre; background: #101820; color: #e9f1f7; padding: 9px; border-radius: 5px; font: 10px/1.18 Consolas, "Courier New", monospace; }
.board-card.unavailable { border-style: dashed; background: #f1f2f3; }
.status-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; background: #eee; }
.badge.exact, .badge.semantic_exact { background: var(--green-bg); color: var(--green); }
.badge.canonicalized_metadata, .badge.different { background: var(--amber-bg); color: var(--amber); }
.badge.state_mismatch, .badge.error, .badge.invalid_canonical { background: var(--red-bg); color: var(--red); }
.canonical-card { margin: 12px 0; border-top: 2px solid var(--bs-tan); padding-top: 10px; }
.comparison-line { color: #4f5964; font-size: 13px; margin-bottom: 7px; }
details { margin-top: 8px; border: 1px solid #d7dee6; border-radius: 7px; overflow: hidden; background: #fff; }
summary { cursor: pointer; font-weight: 700; padding: 8px 10px; background: #f8f9fa; }
.json-compare { border-top: 1px solid #d7dee6; background: #d7dee6; max-height: 390px; overflow: auto; }
.json-head, .json-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1px; }
.json-head.triplet, .json-row.triplet { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.json-head > div { background: #dfe7ef; padding: 9px 11px; font-weight: 700; position: sticky; top: 0; z-index: 1; }
.json-cell { min-width: 0; background: #101820; color: #e8f1f8; padding: 6px 8px; }
.json-cell pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; font: 11px/1.4 Consolas, "Courier New", monospace; }
.json-cell.diff { background: var(--red-bg); color: var(--red); font-weight: 700; }
.diff-table { border-collapse: collapse; width: 100%; table-layout: fixed; }
.diff-table th, .diff-table td { border: 1px solid #ddd; padding: 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
.diff-table td.diff { background: var(--red-bg); color: var(--red); }
.pass-text { color: var(--green); padding: 0 8px; }
.error-text { color: var(--red); }
.fail-card { background: var(--red-bg); }
.board-parity { margin: 12px 0; padding: 10px; border: 1px solid var(--bs-tan); border-radius: 7px; background: #fff; }
.diagnostics > h5 { margin-bottom: 4px; }
@media (max-width: 1500px) { .methods { grid-template-columns: repeat(3, minmax(330px, 1fr)); overflow-x: auto; } }
@media (max-width: 900px) { .reference-lane { grid-template-columns: 1fr; } }
'''


def render_page(case_sections: list[str], provenance: dict[str, Any]) -> str:
    p = e(json.dumps(provenance, indent=2, ensure_ascii=False))
    renderer = provenance.get("renderer", {})
    board_sha = e(renderer.get("resolved_commit") or renderer.get("expected_commit"))
    board_version = e(renderer.get("package_version"))
    board_ref = e(renderer.get("requested_release_ref"))
    calculator = provenance.get("calculator", {})
    calc_sha = e(calculator.get("resolved_release_commit") or calculator.get("release_commit"))
    calc_ref = e(calculator.get("requested_release_ref"))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oracle-first XGID and GNUID comparison</title><style>{CSS}</style></head><body>
<header class="top"><h1>Oracle-first XGID ↔ GNUID verification</h1>
<p>Three method columns: Engine Kit native, Engine Kit public API / bridge, and Direct AnkiGammon. In each method column, real GNU CLI evidence is on top, backgammonboard v0.1.1 BS rendering is underneath, and factual canonical representation follows. Stable players are never swapped for appearance.</p></header>
<div class="provenance"><strong>backgammonboard:</strong> requested {board_ref}, resolved {board_sha}, package {board_version}, BS colors/style · <strong>Calculator:</strong> requested {calc_ref}, resolved {calc_sha}</div>
<main data-layout="three-method-columns">{''.join(case_sections)}
<section class="case"><h2>Provenance</h2><pre>{p}</pre></section></main></body></html>'''
