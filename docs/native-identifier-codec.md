# Native XGID and GNUID codec

The native codec is implemented in:

```text
src/backgammon_engine_kit/position_contract/native_codec.py
```

It uses the existing `UniversalPosition` contract as the canonical state. It does not use AnkiGammon or R at runtime.

## Public API

```python
from backgammon_engine_kit import (
    position_from_xgid,
    position_from_gnuid,
    xgid_from_position,
    gnuid_from_position,
    xgid_to_gnuid,
    gnuid_to_xgid,
)
```

Detailed conversion evidence is available through:

```python
convert_xgid_to_gnuid(...)
convert_gnuid_to_xgid(...)
```

These calls return `NativeConversionResult`, including explicit normalizations and representation losses.

## Stable-player conversion semantics

Identifier conversion has one canonical player-identity policy. Stable
`player_0` and `player_1` do not swap during XGID/GNUID conversion.

The verified mapping is:

| Fact | `player_0` | `player_1` |
| --- | --- | --- |
| XGID visual side | top | bottom |
| XGID checker case | lowercase | uppercase |
| XGID turn | `-1` | `+1` |
| XGID cube owner | `-1` | `+1` |
| XGID score field | second | first |
| GNU player code | `0` | `1` |

GNU Position ID checker blocks are player-relative. The Match ID DiceOwner is
therefore required to assign those blocks back to stable players. Canonical
encoding writes block 0 for the opponent of DiceOwner and block 1 for
DiceOwner, with the required point-number transformation for `player_0`.

For the accepted top-roller regression:

```text
XGID=-BDB-------------a------e-:1:-1:-1:42:0:0:0:5:8
```

the canonical GNUID is:

```text
ewMAAD4gAAAAAA:AQGqAAAAAAAE
```

The XGID maximum-cube exponent `8` is not represented by GNUID, so this
conversion requires `allow_lossy=True`. That loss is unrelated to player
orientation.

The same Position ID paired with:

```text
AQGqAAAAAAAE
UQmqAAAAAAAE
```

does **not** describe one stable position in two perspectives. `AQGq...` assigns
DiceOwner, TurnOwner, cube ownership, and the relative checker rows to stable
`player_0`; `UQmq...` assigns those player-dependent facts to stable
`player_1`. They therefore decode to different canonical states.

GNU's optional interactive command that swaps players after importing an XGID
is a GNU UI/state operation. It is deliberately outside identifier conversion
and no `perspective_policy` is exposed by the native codec.

## GNU Match ID

The implementation follows GNU Backgammon 1.08.003's nine-byte Match ID layout:

| Bits | Field |
| ---: | --- |
| 0-3 | log2 cube value |
| 4-5 | cube owner |
| 6 | player on roll, GNU `fMove` |
| 7 | Crawford |
| 8-10 | GNU game state |
| 11 | decision player, GNU `fTurn` |
| 12 | double offered |
| 13-14 | resignation value |
| 15-17 | higher die |
| 18-20 | lower die |
| 21-35 | match length |
| 36-50 | player 0 score |
| 51-65 | player 1 score |
| 66 | inverse Jacoby-in-use flag |
| 67-71 | reserved |

Bit 66 is not treated as a fixed framing bit in the native codec.

## GNU Position ID

The public Position ID is the 80-bit unary checker representation used by GNU Backgammon's legacy public identifier. The native codec maps stable `player_0` and `player_1` state into GNU's on-roll board ordering only at the encoding boundary.

## Known representation differences

GNUID and XGID do not carry identical metadata. The conversion result reports normalization and loss separately from factual state changes.

For example:

```text
4HPwATDgc/ABMA:8IhuACAACAAE
```

contains GNU lifecycle metadata that XGID does not preserve. The native converter produces:

```text
XGID=-b----E-C---eE---c-e----B-:0:0:1:53:1:2:1:3:10
```

and converting that XGID back to GNUID produces:

```text
4HPwATDgc/ABMA:8IluACAACAAE
```

The factual checker, turn, dice, cube, score, match, Crawford, and Jacoby state is preserved while GNU lifecycle state normalizes from setup to playing.

## Reference behavior

The implementation was written against GNU Backgammon 1.08.003 behavior, especially:

```text
set.c          SetXGID and CommandSetXGID
positionid.c   PositionFromXG and PositionID
matchid.c      MatchID and MatchIDFromMatchState
```

GNU source is used as a behavioral specification. Released `backgammoncalculator` 0.1.0 and its GNU-derived regression corpus are cross-language implementation evidence for the same stable-player model. The Python implementation is original code over Engine Kit's canonical position contract.

## Source and licensing boundary

GNU Backgammon 1.08.003 source was used to determine public identifier behavior and field layout. GNU Backgammon is distributed under GPLv3-or-later. No GNU C source is copied into this module; the native codec is an original Python implementation over Engine Kit's existing canonical model.
