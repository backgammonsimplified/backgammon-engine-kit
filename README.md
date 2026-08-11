# Backgammon Engine Kit

Backgammon Engine Kit is a Python library for turning common backgammon position identifiers into validated, deterministic state that other applications can safely consume.

It provides four main capabilities:

- decode XGID and GNU Backgammon identifiers into explicit position state;
- convert between supported XGID and complete GNUID forms while reporting normalization or representational loss;
- prepare engine-neutral checker-play and cube-analysis requests for supported GNU Backgammon and BGSage configurations;
- produce deterministic renderer-facing JSON with semantic state kept separate from display orientation.

The package is designed for applications that need a stable boundary between position notation, engine analysis, caching, and presentation. Unsupported or source-unknown state is kept explicit rather than guessed.

## Installation

Backgammon Engine Kit requires Python `>=3.8`.

```bash
python -m pip install backgammon-engine-kit
```

Runtime dependencies are installed automatically.

For development from a source checkout:

```bash
python -m pip install -e .
python -m pytest
```

## Quick start

### Decode an XGID

`decode_xgid` returns both the validated Universal Position and a provenance record describing how each fact was obtained.

```python
from backgammon_engine_kit import decode_xgid

xgid = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
decoded = decode_xgid(xgid)

position = decoded.position
source = decoded.source

print(position.schema_version)  # universal-position-v1
print(source.schema_version)    # position-source-v1
print(position.state.on_roll)   # player_1 for this example
```

### Convert XGID and GNUID

The native Python codec converts through Universal Position so stable player ownership is not changed by turn or display perspective.

```python
from backgammon_engine_kit import gnuid_to_xgid, xgid_to_gnuid

xgid = "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
gnuid = xgid_to_gnuid(xgid)
round_trip = gnuid_to_xgid(gnuid)
```

Some source facts do not exist in the target format. Strict conversion rejects such cases unless the caller explicitly opts into documented loss with `allow_lossy=True`. Detailed conversion functions, `convert_xgid_to_gnuid` and `convert_gnuid_to_xgid`, return normalization and loss records alongside the identifier.

### Prepare an analysis request

The identifier bridge can prepare a validated `AnalysisRequest` for GNU Backgammon or BGSage.

```python
from backgammon_engine_kit import to_gnu_request

prepared = to_gnu_request(
    "4PPgASTgc/ABMA:cAnqAAAAAAAE",
    "checker",
)

if prepared.ready:
    request = prepared.request
else:
    print(prepared.status, prepared.missing_state, prepared.unsupported_state)
```

These bridge functions prepare requests; they do not execute an engine. A lone GNU Position ID can be parsed, but analysis preparation remains unavailable until missing turn, dice, cube, score, and match context is supplied.

### Produce renderer JSON

Renderer Position combines a validated semantic position with a separate display view.

```python
from backgammon_engine_kit import (
    renderer_position_from_xgid,
    renderer_position_json,
)

renderer_position = renderer_position_from_xgid(
    "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
)

print(renderer_position_json(renderer_position))
```

The renderer helpers do not render SVG, HTML, PNG, or another board image. They produce a deterministic transport envelope for a renderer to consume.

## Player identity and orientation

Engine Kit deliberately separates stable player identity from turn and display orientation.

Two public compatibility surfaces use different stable labels:

| Surface | Top / X / GNU player 0 | Bottom / O / GNU player 1 |
|---|---|---|
| Identifier analysis bridge | `player_x` | `player_o` |
| Universal Position and native codec | `player_0` | `player_1` |

The labels correspond 1:1. A change of player on roll never swaps checker ownership, cube ownership, or scores. Display rotation is presentation state only.

Applications should use the labels defined by the API surface they are consuming rather than translating them from whose turn it is.

## Supported position formats

Version 0.3.0 intentionally supports a bounded, validated subset of identifier state.

- **XGID:** complete `XGID=` form using the strict 15-checker profile.
- **GNUID:** complete 14-character GNU Position ID plus 12-character GNU Match ID, separated by `:`.
- **GNU Position ID only:** accepted by the analysis bridge as incomplete source state; additional context is required before a ready analysis request can be produced.

The [Universal Position support matrix](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/UNIVERSAL_POSITION_V1_SUPPORT_MATRIX.md) records which facts are decoded, derived, require external context, are unsupported, or are not represented by each source format.

## Verified engine configurations

Engine execution is evidence-gated. Version 0.3.0 has verified checker-play and cube-analysis support for:

- GNU Backgammon `1.08.003` at the verified `1ply` configuration;
- BGSage `1.2.20260706` at the verified `1ply` configuration.

Other GNU and BGSage settings are not implied by the presence of an engine adapter. Unsupported or unverified configurations remain unavailable rather than silently falling back to another setting. BGSage rollout is not supported in 0.3.0.

See [Engine analysis contracts](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/ENGINE_ANALYSIS_CONTRACTS.md) for the request, result, cache, and engine-evidence boundaries.

## Command-line interface

Renderer commands accept supported complete XGID and GNUID forms:

```bash
backgammon-engine-kit render-xgid \
  'XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10'

backgammon-engine-kit render-gnuid \
  'PAAAICMAAAAAAA:cAkAAAAAAAAE'
```

The JSON command interface reads one JSON object from standard input and writes one deterministic JSON object to standard output:

```bash
printf '%s\n' '{"operation":"capabilities"}' | python -m backgammon_engine_kit
```

Supported JSON operations are `capabilities`, `validate_configuration`, `validate_request`, `cache_key`, `cache_lookup`, `analyze`, and `analyze_fixture`.

Run `backgammon-engine-kit --help` or the relevant subcommand with `--help` for command details.

## Architecture and reference documentation

The public architecture records describe the package contracts and their boundaries:

- [Architecture index](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/README.md)
- [Engine analysis contracts](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/ENGINE_ANALYSIS_CONTRACTS.md)
- [Universal Position v1 implementation](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/UNIVERSAL_POSITION_V1_IMPLEMENTATION.md)
- [Universal Position v1 support matrix](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/UNIVERSAL_POSITION_V1_SUPPORT_MATRIX.md)
- [Renderer Position interface](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/docs/architecture/RENDERER_POSITION_INTERFACE.md)

A consumer-oriented identifier/request example is available at [`examples/node_identifier_bridge.py`](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/examples/node_identifier_bridge.py).

## Design principles

Engine Kit follows a few deliberately conservative rules:

- preserve factual state rather than relying on identifier spelling alone;
- keep stable player identity independent of player on roll and display perspective;
- preserve source provenance and normalization effects;
- represent unknown or unsupported facts explicitly;
- keep semantic position state separate from renderer view state;
- make serialized output and cache identity deterministic;
- fail closed when an adapter requires state that the source cannot establish.

These constraints are intended to make the package predictable at integration boundaries, even when source formats differ in what they can represent.

## Project status

`0.3.0` is the first public release line. Its API is intentionally bounded rather than an assertion that every historical XGID/GNUID variant or every engine configuration is supported.

Release validation covers package build and metadata checks, clean wheel and source-distribution installation, public exports, schemas, representative identifier/API behavior, deterministic CLI behavior, and the full Python test suite.

See [`CHANGELOG.md`](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/CHANGELOG.md) for notable release changes.

## Contributing

Bug reports and focused improvements are welcome. Before opening a pull request, read [`CONTRIBUTING.md`](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/CONTRIBUTING.md) for scope, validation, and compatibility expectations.

## Licensing

Backgammon Engine Kit follows the Backgammon Simplified mixed-license policy:

- **Software:** GNU Affero General Public License, version 3 only (`AGPL-3.0-only`).
- **Original explanatory and educational material:** Creative Commons Attribution-ShareAlike 4.0 International (`CC-BY-SA-4.0`).
- **Backgammon Simplified name, logo, and distinctive official branding:** no trademark rights are granted by those licenses.
- **Third-party material:** remains under its original license or terms.

See [`LICENSE.md`](https://github.com/backgammonsimplified/backgammon-engine-kit/blob/master/LICENSE.md) for the authoritative scope mapping and attribution rules.
