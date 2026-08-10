# Universal Position v1 bounded implementation

## Scope

This milestone implements three separate immutable contracts:

- `universal-position-v1`: engine-relevant semantic state;
- `position-source-v1`: raw source, parser identity, stable player mapping,
  field origins, external context, warnings, assumptions, and losses;
- `backgammon-view-v1`: display orientation only.

It does not migrate `AnalysisService`, request or result wire formats, the
production cache namespace, or the legacy `NormalizedPosition` compatibility
surface.

## Source profiles

### `xgid-v1-15-checker`

- XGID top maps to `player_0`.
- XGID bottom maps to `player_1`.
- The named profile fixes 15 checkers per player.
- Point and bar populations are decoded.
- Borne-off counts are derived from checker count minus represented points and
  bar and are recorded as derived origins.
- `cube.enabled` remains null before enrichment.

### `gnubg-combined-id-v1-15-checker`

- A GNU Position ID and Match ID are both required.
- GNU player 0 maps to `player_0`; GNU player 1 maps to `player_1`.
- The named profile fixes 15 checkers per player.
- Position ID point and bar populations are decoded independently by Engine Kit.
- Match ID DiceOwner, TurnOwner, dice, cube, pending offer, scores, match length,
  Crawford, and game state are decoded where represented.
- Source-omitted rules and `cube.enabled` remain null before enrichment.

## Enrichment

Enrichment is pure. It returns new immutable position and source records.
External settings can fill only null fields. They cannot overwrite decoded,
profile-fixed, or derived facts, and they cannot supply phase, decision player,
decision type, or pending action. Every supplied leaf receives a granular
`supplied_externally` origin.

## Semantic validation

JSON Schema validation is followed by implementation-level semantic validation.
The latter checks checker conservation, physical point ownership, score and match
state, cube powers and maximum, pending-action identities, phase and decision
state derivation, Crawford, ordinary doubles, beavers, raccoons, and resignation
consistency. Unknown facts never grant permissive cube legality.

## Hashes

- `semantic_state_hash` validates and hashes only `universal-position-v1`.
- `source_record_hash` validates and hashes provenance without its self-hash.
- `view_hash` validates and hashes only `backgammon-view-v1`.

Equivalent enriched XGID and GNU sources therefore share a semantic hash while
retaining distinct source-record hashes.

## Adapter boundaries

The GNU seam re-decodes the preserved combined ID, reapplies the exact recorded
external settings, validates the reconstructed semantic state, recomputes the
semantic hash, and refuses invocation on mismatch.

The BGSage seam converts stable canonical players to one current-player-relative
26-slot board at the final boundary. Required unknown context causes a specific
failure rather than a silent default.
