# Engine analysis contracts

This document describes the public analysis request/result boundary, cache identity, and the engine configurations verified for Backgammon Engine Kit 0.3.0.

## Analysis request

`analysis-request-v2` accepts one supported position representation:

- a verified XGID;
- a complete GNU Position ID plus Match ID in `gnuid` form; or
- a complete `normalized-position-v1` value.

A request also specifies one engine (`sage` or `gnu`), one `analysis_setting`, and explicit checker-play or cube-analysis context.

Checker-play requests require two dice. Cube-analysis requests require `dice: null`.

The configuration is immutable and content-hashed. Engine, model, and weights identities remain `null` when they have not been verified rather than being inferred from local installation state.

## Analysis result

`analysis-result-v2` preserves:

- position identity and source format;
- engine metadata;
- configuration trace;
- decision type;
- warnings and assumptions;
- timestamps; and
- immutable raw output or a content-addressed raw-output reference.

A successful checker-play result contains a non-null `checker_decision` and a null `cube_decision`. A successful cube result has the inverse. A failed result has both decision sections null and includes a structured failure code.

Unavailable candidate/action measurements remain explicit JSON `null` values. Checker candidates can identify the source of normalized notation. Cube decisions can preserve an engine's exact raw recommendation independently from the normalized action identifier. The retained GNU-specific recommendation field remains compatible with the verified GNU fixtures.

## Cache identity

The SHA-256 cache key covers:

- position state;
- engine;
- analysis setting;
- decision context;
- `analysis-result-v2`;
- verified engine/model/weights identity; and
- configuration hash.

Report mode contributes to cache identity only when the caller declares that it changes the produced data.

Lookup returns either:

- `hit` with a validated result; or
- `miss` with `result: null`.

Only complete analysis results may be stored.

## Verified GNU Backgammon configuration

GNU Backgammon `1.08.003` `1ply` checker-play and cube transcripts back the strict single-position parser and bounded shell-free adapter.

The adapter requires the verified executable, neural-network, bearoff, match-equity, invocation, and configuration identities. It preserves GNU-emitted actual ply separately from the requested setting; move-filtered checker candidates may therefore retain an emitted `actual_ply` of zero.

## Verified BGSage configuration

BGSage `1.2.20260706` `1ply` checker-play and cube JSON artifacts back the strict fresh-process adapter.

The adapter pins the Python/native module, Stage 9 weight set, bearoff database, GNU-ID context parser, invocation, and configuration identities. BGSage's emitted `1-ply` remains separate from the requested setting. Its five emitted probability fields are preserved, while the unavailable aggregate loss probability remains `null`.

## Configuration support boundary

Engine support is evidence-gated. The presence of an adapter does not imply support for every setting exposed by the underlying engine.

For 0.3.0:

- the verified GNU configuration is `1ply` checker play and cube analysis;
- the verified BGSage configuration is `1ply` checker play and cube analysis;
- other GNU and BGSage settings remain unavailable unless they are represented by the retained evidence manifest and verified configuration identity;
- BGSage rollout is intentionally unsupported.

BGSage rollout, where referenced by the API, is exactly:

```json
{"engine":"sage","analysis_setting":"rollout"}
```

Unsupported configurations should produce an explicit unavailable/unsupported outcome rather than silently substituting a different analysis setting.
