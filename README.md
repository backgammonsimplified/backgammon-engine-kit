# Backgammon Engine Kit

Backgammon Engine Kit is a private, pre-release Python package for bounded,
engine-neutral backgammon position contracts and analysis requests:

```text
position + engine + analysis setting -> checker or cube result
```

It provides immutable request and result models, deterministic JSON and cache
identity, capability reporting, structured failures, and the Universal Position
v1 contract. GNU Backgammon 1.08.003 and BGSage 1.2.20260706 checker/cube
analysis at their verified `1ply` configurations are evidence-backed. Other
GNU and Sage settings, including Sage rollout, remain unavailable.

## Requirements and installation

The package declares Python `>=3.7`. This baseline was tested only on Python
3.12.8; it does not claim testing on other Python versions.

```bash
python -m pip install .
```

For development:

```bash
python -m pip install -e .
python -m pytest
```

## Universal Position example

Decode an XGID into the immutable canonical position and source records:

```python
from backgammon_engine_kit import decode_xgid

decoded = decode_xgid("XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10")
position = decoded.position
source = decoded.source

print(position.schema_version)  # universal-position-v1
print(source.schema_version)    # position-source-v1
```

The XGID and GNU combined-ID decoders have intentionally bounded support. See
the [support matrix](docs/architecture/UNIVERSAL_POSITION_V1_SUPPORT_MATRIX.md)
for represented, external-context, and unsupported state.

## Renderer Position

The renderer-facing API composes separate validated Universal Position and
Backgammon View contracts with their respective hashes:

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

The CLI accepts the supported XGID and GNU combined-ID forms:

```bash
backgammon-engine-kit render-xgid \
  'XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10'
backgammon-engine-kit render-gnuid \
  'PAAAICMAAAAAAA:cAkAAAAAAAAE'
```

Successful renderer commands emit only deterministic envelope JSON. They do not
invoke GNU Backgammon or Sage and do not render SVG or another board image. See
the [Renderer Position interface](docs/architecture/RENDERER_POSITION_INTERFACE.md)
for Python signatures, view defaults, orientation, unknown facts, and CLI
failure behavior.

## JSON CLI and evidence fixtures

The foreground CLI reads one JSON object from standard input and writes one
deterministic JSON object to standard output:

```bash
printf '%s\n' '{"operation":"capabilities"}' | python -m backgammon_engine_kit
```

Supported operations are `capabilities`, `validate_configuration`,
`validate_request`, `cache_key`, `cache_lookup`, `analyze`, and
`analyze_fixture`. Fixture replay verifies retained checksums and reparses raw
transcripts without launching either engine.

The repository retains checksum-controlled GNU and Sage evidence under
`evidence/` for reproducible development tests. The installed wheel contains
the package and its JSON schemas, not tests, evidence, fixtures, or developer
scripts.

See [Architecture](docs/architecture/README.md) and
[fixtures/README.md](fixtures/README.md) for the contract and evidence policy.
