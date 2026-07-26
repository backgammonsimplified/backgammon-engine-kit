# Engine analysis contracts

## Request

`analysis-request-v2` accepts a verified XGID, a combined GNU Position ID and
Match ID in `gnuid` format, or a complete `normalized-position-v1`, one engine (`sage` or `gnu`), one ordinary
`analysis_setting`, and explicit checker/cube context. A checker request must
carry two dice; a cube request must carry `dice: null`.

The configuration is immutable and content-hashed. Versions and model/weights
identities remain `null` when they have not been verified.

## Result

`analysis-result-v2` preserves position identity/format, engine metadata,
configuration trace, decision type, warnings, assumptions, timestamps, and an
immutable raw output or content-addressed raw reference.

A successful checker result has non-null `checker_decision` and null
`cube_decision`. A successful cube result has the inverse. A failed result has
both decision sections null and a structured failure code.

Unavailable candidate/action measurements remain explicit JSON `null` values.
Checker candidates can identify the source of normalized notation. Cube
decisions can preserve an engine's exact raw recommendation independently of
the normalized action identifier; the retained GNU-specific recommendation
field remains compatible with the accepted GNU fixtures.

## Cache

The SHA-256 key covers position, engine, analysis setting, decision context,
`analysis-result-v2`, verified engine/model/weights identity, and configuration
hash. Report mode enters the identity only when the caller declares it changes
the produced data.

Lookup returns `hit` with a validated result or `miss` with `result: null`.
Only complete analysis results may be stored.

## Evidence gate

GNU Backgammon 1.08.003 `1ply` checker and cube transcripts now back a strict
single-position parser and bounded shell-free adapter. The adapter requires the
verified executable, neural-network, bearoff, match-equity, invocation, and
configuration identities. It preserves GNU-emitted actual ply separately from
the requested setting; move-filtered checker candidates may therefore retain
an emitted `actual_ply` of zero.

BGSage 1.2.20260706 `1ply` checker and cube JSON artifacts now back a strict
fresh-process adapter. The adapter pins the Python/native module, stage9 weight
set, bearoff database, GNU-ID context parser, invocation, and configuration
identities. BGSage's emitted `1-ply` stays separate from the requested setting.
Its five emitted probability fields are preserved, while the unavailable
aggregate loss probability remains null.

All other GNU settings and all other Sage settings remain evidence gated by
`fixtures/evidence_manifest.json`. Sage rollout is deliberately unsupported in
this milestone.

Sage rollout is only `{"engine":"sage","analysis_setting":"rollout"}`.
