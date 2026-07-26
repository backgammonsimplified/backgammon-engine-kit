# Renderer Position interface

## Purpose and contract separation

`RendererPosition` is the minimum renderer-facing transport envelope. It keeps
the accepted contracts separate:

- `position` is a validated `universal-position-v1` object containing checker,
  turn, dice, cube, score, match, and rule semantics;
- `semantic_state_hash` is the SHA-256 identity of that validated position;
- `view` is a validated `backgammon-view-v1` object containing display
  orientation choices only;
- `view_hash` is the SHA-256 identity of that validated view.

The envelope is not a fourth semantic position model. It does not flatten or
duplicate nested fields, invoke an engine, perform analysis, or render SVG,
HTML, PNG, or another board image.

## Python API

Create an envelope from a validated Universal Position and an explicit view:

```python
from backgammon_engine_kit import (
    BackgammonView,
    create_renderer_position,
    renderer_position_json,
)

view = BackgammonView(
    top_player="player_1",
    bottom_player="player_0",
    point_labels_for="player_1",
    bottom_home_board_side="left",
    cube_display_side="right",
    rotation="rotated",
    view_origin="external",
)
renderer_position = create_renderer_position(position, view)
json_text = renderer_position_json(renderer_position)
```

Omit `view` to use the stable generated default:

```python
renderer_position = create_renderer_position(position)
```

Decode supported source identifiers directly:

```python
from backgammon_engine_kit import (
    renderer_position_from_gnuid,
    renderer_position_from_xgid,
)

xgid_result = renderer_position_from_xgid(
    "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
)
gnu_result = renderer_position_from_gnuid(
    "PAAAICMAAAAAAA:cAkAAAAAAAAE"
)
```

Each decoder factory also accepts an explicit `view` and optional
`external_settings`. External settings use the existing pure enrichment layer
and may fill only facts that the source left unknown. They cannot overwrite a
decoded or derived fact.

The public signatures are:

```python
default_backgammon_view()
create_renderer_position(position, view=None)
renderer_position_from_xgid(raw_identifier, view=None, external_settings=None)
renderer_position_from_gnuid(
    combined_id,
    view=None,
    external_settings=None,
    runtime_version="GNU Backgammon 1.08.003",
)
renderer_position_json(renderer_position)
```

Constructing `RendererPosition` directly verifies both nested contracts and
rejects either supplied hash when it does not match its object.

## Supported identifier forms

- XGID uses the existing strict `xgid-v1-15-checker` profile. A complete
  `XGID=` board plus its nine metadata fields is required.
- GNU uses the existing strict `gnubg-combined-id-v1-15-checker` profile. A
  14-character GNU Position ID and a 12-character GNU Match ID are both
  required, separated by one colon.

No other historical identifier spelling is inferred or accepted.

## CLI

The renderer commands emit only one envelope JSON object on standard output:

```bash
backgammon-engine-kit render-xgid \
  'XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10'

backgammon-engine-kit render-gnuid \
  'PAAAICMAAAAAAA:cAkAAAAAAAAE'
```

The equivalent module commands are:

```bash
python -m backgammon_engine_kit render-xgid 'XGID=...'
python -m backgammon_engine_kit render-gnuid 'PositionID:MatchID'
```

Use `--external-settings-json` to fill explicit source-unknown facts and
`--view-json` to supply a complete Backgammon View v1 object. Run
`backgammon-engine-kit --help`, `render-xgid --help`, or
`render-gnuid --help` for the bounded command help.

Malformed, incomplete, unsupported, inconsistent, or schema-invalid inputs
produce a diagnostic on standard error and status 2. Successful standard output
is UTF-8 JSON with LF line endings and exactly one final newline.

## Orientation and stable player slots

The identities `player_0` and `player_1` remain stable regardless of turn,
colour, or rotation.

- XGID top maps to `player_0`, bottom maps to `player_1`, and the accepted XGID
  source view is retained unless an explicit view is supplied.
- GNU player 0 maps to `player_0` and GNU player 1 maps to `player_1`. GNU
  combined IDs do not contain an arbitrary display view, so the generated
  default is used.
- The generated default places `player_0` on top and `player_1` on the bottom,
  labels points for `player_0`, puts the bottom home board on the right, and
  displays the cube on the left.
- `bottom_home_board_side` establishes physical point placement. In the
  Universal Position self-relative coordinate system, player 0 point `n` and
  player 1 point `25 - n` are the same physical point.
- `cube_display_side` is presentation only and never implies cube ownership.

## Unknown facts and determinism

Missing source facts remain explicit `null` values in Universal Position. The
renderer interface does not guess cube availability, variation, match rules, or
other unavailable context. Callers may provide such context only through the
validated enrichment mechanism.

For the same accepted input, explicit context, view, and package version,
`renderer_position_json` returns identical JSON text. Keys are sorted, hashes
bind only their respective nested contracts, source spelling is excluded from
semantic identity, and view choices do not affect semantic identity. The output
contains no timestamp, process, machine, username, or temporary-path data.
