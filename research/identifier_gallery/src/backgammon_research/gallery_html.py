from __future__ import annotations
import html, json
from typing import Any


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def visual(identifier: str, renderer_record: dict[str, Any] | None, gnu_record: dict[str, Any] | None, label: str) -> str:
    if identifier.startswith("XGID="):
        if renderer_record and renderer_record.get("output"):
            body = f'<div class="svg">{renderer_record["output"]}</div>'
        else:
            body = f'<pre>{e((renderer_record or {}).get("stderr") or "Renderer unavailable")}</pre>'
    else:
        body = f'<pre class="gnu-board">{e((gnu_record or {}).get("board") or "GNU CLI board unavailable")}</pre>'
    return f'<div class="endpoint"><h5>{e(label)}</h5><code>{e(identifier)}</code>{body}</div>'


def diff_table(rows: list[dict[str, Any]], title: str) -> str:
    if not rows:
        return f'<details><summary>{e(title)}: no differences</summary><p class="pass-text">Canonical states align.</p></details>'
    body = ''.join(
        f'<tr><td><code>{e(r["path"])}</code></td><td>{e(json.dumps(r["left"], ensure_ascii=False))}</td><td class="diff">{e(json.dumps(r["right"], ensure_ascii=False))}</td></tr>'
        for r in rows
    )
    return f'<details><summary>{e(title)}: {len(rows)} difference(s)</summary><table class="diff-table"><tr><th>Path</th><th>Reference/source</th><th>Method/result</th></tr>{body}</table></details>'


def method_card(attempt: dict[str, Any], visuals: dict[str, Any]) -> str:
    cls = "pass" if attempt["reference_exact"] else ("warn" if attempt["reference_semantic"] else "fail")
    rt = "pass" if attempt["roundtrip_exact"] else ("warn" if attempt["roundtrip_semantic"] else "fail")
    rt_label = "exact" if attempt["roundtrip_exact"] else ("semantic" if attempt["roundtrip_semantic"] else "changed")
    badges = f'<span class="badge {cls}">{e(attempt["classification"])}</span><span class="badge {rt}">round trip: {rt_label}</span>'
    if attempt["status"] != "ok":
        return f'<article class="method fail-card"><h4>{e(attempt["label"])}</h4>{badges}<pre>{e(attempt["error"])}</pre></article>'
    return (
        f'<article class="method"><h4>{e(attempt["label"])}</h4><div class="badges">{badges}</div>'
        f'<div class="lane">{visual(attempt["source"], visuals["source_render"], visuals["source_gnu"], "Source")}'
        f'<div class="arrow">→</div>{visual(attempt["middle"], visuals["middle_render"], visuals["middle_gnu"], "Converted")}'
        f'<div class="arrow">→</div>{visual(attempt["terminal"], visuals["terminal_render"], visuals["terminal_gnu"], "Round trip")}</div>'
        f'{diff_table(attempt["middle_diff_from_reference"], "Converted canonical state vs Calculator 0.2.0")}'
        f'{diff_table(attempt["roundtrip_diff_from_source"], "Round-trip canonical state vs source")}</article>'
    )


def reference_card(direction: str, source: str, middle: str, terminal: str, visuals: dict[str, Any], bglab_output: str | None = None, gnu_post_import: str | None = None) -> str:
    diagnostics = []
    if gnu_post_import:
        diagnostics.append(f'<p><strong>GNU post-import diagnostic:</strong> <code>{e(gnu_post_import)}</code></p>')
    if bglab_output:
        diagnostics.append(f'<p><strong>Diagnostic: R bglab:</strong> <code>{e(bglab_output)}</code></p>')
    return (
        '<article class="reference"><h4>Reference: backgammoncalculator 0.2.0</h4>'
        f'<p>{e(direction)}</p><div class="lane">{visual(source, visuals["source_render"], visuals["source_gnu"], "Source")}'
        f'<div class="arrow">→</div>{visual(middle, visuals["middle_render"], visuals["middle_gnu"], "Converted")}'
        f'<div class="arrow">→</div>{visual(terminal, visuals["terminal_render"], visuals["terminal_gnu"], "Round trip")}</div>'
        + ''.join(diagnostics) + '</article>'
    )


CSS = r''':root{--ink:#17212b;--line:#d7dee6;--navy:#102a43;--green:#176b2c;--greenbg:#eaf7ed;--amber:#8a5a00;--amberbg:#fff4ce;--red:#b42318;--redbg:#ffebe9}*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:var(--ink);font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif}.top{background:var(--navy);color:white;padding:26px 30px}.top p{max-width:1300px;color:#d7e3ef}.provenance{font-size:12px;background:#091b2b;color:#dce8f4;padding:12px 30px;overflow:auto}main{max-width:2200px;margin:auto;padding:22px}.case{margin-bottom:42px}.case>h2{border-bottom:3px solid #243b53;padding-bottom:8px}.direction{background:#fff;border:1px solid var(--line);border-left:7px solid #829ab1;border-radius:10px;padding:16px;margin-bottom:22px}.reference{border:2px solid #486581;background:#f0f6fb;border-radius:9px;padding:14px;margin-bottom:16px}.methods{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.method{min-width:0;border:1px solid #b9c6d3;border-radius:9px;padding:12px;background:#fbfcfd}.method h4,.reference h4{margin:0 0 9px;font-size:17px}.lane{display:grid;grid-template-columns:minmax(0,1fr) 28px minmax(0,1fr) 28px minmax(0,1fr);gap:7px;align-items:start}.arrow{text-align:center;font-size:25px;padding-top:80px;color:#829ab1}.endpoint{min-width:0}.endpoint h5{margin:0 0 5px}.endpoint code{display:block;background:#101820;color:#fff;padding:8px;border-radius:5px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;min-height:55px}.svg{height:255px;background:white;border:1px solid var(--line);overflow:auto;margin-top:7px}.svg svg{width:100%;height:auto;max-height:245px}.gnu-board{height:255px;background:#111;color:#eee;padding:8px;overflow:auto;white-space:pre;font:10px/1.18 Consolas,monospace;margin-top:7px}.badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}.badge{border-radius:999px;padding:4px 8px;border:1px solid var(--line);font-size:12px}.badge.pass{background:var(--greenbg);color:var(--green)}.badge.warn{background:var(--amberbg);color:var(--amber)}.badge.fail{background:var(--redbg);color:var(--red)}details{margin-top:10px;border:1px solid var(--line);border-radius:6px;background:#fff}summary{cursor:pointer;font-weight:700;padding:8px}.diff-table{border-collapse:collapse;width:100%;table-layout:fixed}.diff-table th,.diff-table td{border:1px solid var(--line);padding:6px;vertical-align:top;overflow-wrap:anywhere}.diff-table td.diff{background:var(--redbg)}.pass-text{color:var(--green);padding:0 8px}.fail-card{background:var(--redbg)}@media(max-width:1450px){.methods{grid-template-columns:1fr}.lane{grid-template-columns:1fr}.arrow{padding:0;transform:rotate(90deg)}}'''


def render_page(case_sections: list[str], provenance: dict[str, Any]) -> str:
    p = e(json.dumps(provenance, indent=2, ensure_ascii=False))
    board_sha = e(provenance.get("renderer", {}).get("remote_sha"))
    calc_sha = e(provenance.get("calculator", {}).get("release_commit"))
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Backgammon Identifier Oracle-First Gallery</title><style>{CSS}</style></head><body><header class="top"><h1>Backgammon Identifier Oracle-First Gallery</h1><p>Reference: backgammoncalculator 0.2.0. Methods: Native Python, Engine Kit public API, Direct AnkiGammon. GNU post-import and R bglab are diagnostics. Stable players are never swapped for equivalence.</p></header><div class="provenance"><strong>Renderer:</strong> backgammonboard source commit {board_sha} · <strong>Calculator:</strong> {calc_sha}</div><main>{''.join(case_sections)}<section class="case"><h2>Provenance</h2><pre>{p}</pre></section></main></body></html>'''
