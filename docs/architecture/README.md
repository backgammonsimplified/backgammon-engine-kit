# Architecture and reference

These documents define the public contract boundaries used by Backgammon Engine Kit 0.3.0.

- [Engine analysis contracts](ENGINE_ANALYSIS_CONTRACTS.md): request/result models, cache identity, and verified engine configurations.
- [Universal Position v1 implementation](UNIVERSAL_POSITION_V1_IMPLEMENTATION.md): canonical semantic state, provenance, enrichment, hashing, and adapter boundaries.
- [Universal Position v1 decoder support matrix](UNIVERSAL_POSITION_V1_SUPPORT_MATRIX.md): which XGID and GNUID facts are decoded, derived, externally supplied, unsupported, or unrepresented.
- [Renderer Position interface](RENDERER_POSITION_INTERFACE.md): deterministic renderer transport and the separation between semantic state and display view.

## Reading guide

For application integration, start with the root [`README.md`](../../README.md) and then use the document that matches your boundary:

- converting or decoding identifiers: Universal Position implementation and support matrix;
- preparing or executing engine analysis: Engine analysis contracts;
- producing renderer-facing JSON: Renderer Position interface.

## Stable player naming

Two public compatibility surfaces use different stable labels:

- the identifier analysis bridge uses `player_x` and `player_o`;
- Universal Position and the native identifier codec use `player_0` and `player_1`.

They map directly: `player_x == player_0` is XGID top/X and GNU player 0; `player_o == player_1` is XGID bottom/O and GNU player 1. Neither naming scheme changes when the player on roll or display orientation changes.
