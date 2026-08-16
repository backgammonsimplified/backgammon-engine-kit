# Universal Position v1 implementation

Universal Position v1 is the canonical semantic-state boundary used by the native identifier codec and renderer-facing APIs.

## Contract separation

The implementation defines three separate immutable contracts:

- `universal-position-v1`: engine-relevant semantic state;
- `position-source-v1`: raw source, parser identity, stable player mapping, field origins, external context, warnings, assumptions, and representational losses;
- `backgammon-view-v1`: display orientation only.

These contracts intentionally keep source provenance and presentation choices outside semantic position identity.

The Universal Position surface does not replace the existing `AnalysisService`, analysis request/result wire formats, production cache namespace, or legacy `NormalizedPosition` compatibility surface.

## Stable players

Universal Position uses stable `player_0` and `player_1` identities.

- XGID top maps to `player_0`.
- XGID bottom maps to `player_1`.
- GNU player 0 maps to `player_0`.
- GNU player 1 maps to `player_1`.

Player identity does not rotate with the player on roll or with a renderer view.

The older identifier analysis bridge retains its public `player_x` / `player_o` labels. Those map directly to `player_0` / `player_1`, respectively.

## Source profiles

### `xgid-v1-15-checker`

- The profile requires a complete XGID.
- The profile fixes 15 checkers per player.
- Point and bar populations are decoded.
- Borne-off counts are derived from checker count minus represented points and bar and are recorded with derived origins.
- Turn, dice/pending action, cube state, score, match length, and represented rule state are decoded from XGID metadata.
- `cube.enabled` remains `null` before enrichment because XGID does not establish whether cubeful play is enabled.

### `gnubg-combined-id-v1-15-checker`

- A GNU Position ID and Match ID are both required.
- The profile fixes 15 checkers per player.
- Position ID point and bar populations are decoded independently by Engine Kit.
- Match ID DiceOwner, TurnOwner, dice, cube, pending offer, scores, match length, Crawford, and game state are decoded where represented.
- Source-omitted rules and `cube.enabled` remain `null` before enrichment.

## Enrichment

Enrichment is pure: it returns new immutable position and source records rather than mutating a decoded value.

External settings can fill only `null` fields. They cannot overwrite decoded, profile-fixed, or derived facts, and they cannot supply phase, decision player, decision type, or pending action. Every supplied leaf receives a granular `supplied_externally` origin.

This rule allows consumers to provide context the identifier genuinely omits without turning external defaults into higher-authority facts.

## Semantic validation

JSON Schema validation is followed by implementation-level semantic validation.

Semantic validation checks, among other invariants:

- checker conservation;
- physical point ownership;
- score and match state;
- cube powers and maximum;
- pending-action identities;
- phase and decision-state derivation;
- Crawford state;
- ordinary doubles;
- beavers and raccoons; and
- resignation consistency.

Unknown facts do not grant permissive cube legality. If legality depends on a fact that is not established, the result remains unknown or the downstream operation fails closed as required by its contract.

## Hashes

- `semantic_state_hash` validates and hashes only `universal-position-v1`.
- `source_record_hash` validates and hashes provenance without its self-hash.
- `view_hash` validates and hashes only `backgammon-view-v1`.

Equivalent enriched XGID and GNU sources can therefore share a semantic hash while retaining distinct source-record hashes. Different display views can share the same semantic hash while receiving different view hashes.

## Adapter boundaries

The GNU adapter seam re-decodes the preserved complete GNUID, reapplies the exact recorded external settings, validates the reconstructed semantic state, recomputes the semantic hash, and refuses invocation on mismatch.

The BGSage seam converts stable canonical players to the current-player-relative 26-slot board expected by BGSage only at the final adapter boundary. Required unknown context causes a specific failure rather than a silent default.

## Native identifier conversion

The native Python codec converts XGID and complete GNUID through Universal Position v1.

Format-specific point ordering and GNU player-relative Position ID rows are treated as serialization details. Conversion does not exchange stable player-dependent factual state when turn changes.

When the target format cannot represent a source fact, strict conversion raises `NativeIdentifierCodecError`. Callers may opt into documented loss with `allow_lossy=True`; detailed conversion functions return `NormalizationChange` and `ConversionLoss` records so that normalization is visible to the consumer.
