# Changelog

All notable user-facing changes to Backgammon Engine Kit are documented in this file.

The format follows the principles of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Backgammon Engine Kit uses semantic version numbers for public releases.

## Unreleased

## 0.4.0 - 2026-08-19

### Added

- Configurable Sage checker and cube settings from `1ply` through `4ply`, with independent checker/cube targets and pinned deterministic thread/seed configuration.
- Configurable GNU checker and cube depths from 1 through 4 plies, with the complete GNU Normal move-filter profile encoded in the public configuration identity.
- Runtime verification of configured GNU checker depth, cube depth, thread count, and move-filter rows from GNU's own reported settings.
- Candidate-level `actual_ply` preservation so GNU checker candidates may correctly report shallower actual evaluation depth than the configured target after move filtering.
- Documentation for configured-versus-actual GNU checker depth and for the Benchmarker/Engine Kit ownership boundary.

### Changed

- Legacy `0.3.0` one-ply Sage and GNU configuration objects remain byte-for-byte compatible when the original one-ply constructors are requested.
- Higher-ply Sage checker/cube results fail closed when top-level emitted depth differs from the requested setting; lower-ply retained Sage checker candidates remain explicitly labeled rather than being promoted.
- GNU cube analysis continues to require actual cube depth to equal the configured cube target, while GNU checker results distinguish configured target from candidate actual depth.

### Validation notes

- The historical benchmark profile validated for production commissioning is Sage checker `4ply`, Sage cube `3ply`, GNU checker configured target `3ply`, GNU cube `2ply`, GNU Normal move filter `normal-v1`, one thread per engine, and Sage seed `42`.
- A bounded real-runtime smoke on the pinned runtime observed GNU's recommended checker candidate at actual depth 2 while the configured GNU checker target remained 3-ply; this is expected under the pinned Normal move filter.
- Support for representing numeric 1-4 ply settings does not claim retained runtime evidence for every possible combination. Consumers should use campaign-specific commissioning evidence when relying on a higher-ply profile.

## 0.3.0 - 2026-08-16

### Added

- Immutable engine-neutral analysis request and result contracts with deterministic serialization and cache identity.
- Verified GNU Backgammon `1.08.003` checker-play and cube-analysis support at the evidence-backed `1ply` configuration.
- Verified BGSage `1.2.20260706` checker-play and cube-analysis support at the evidence-backed `1ply` configuration.
- Universal Position v1, Position Source v1, and Backgammon View v1 contracts.
- Strict XGID and complete GNUID decoding with explicit provenance, unknown-state handling, and semantic validation.
- Native Python XGID/GNUID conversion with stable player identity, normalization reporting, and explicit representational-loss reporting.
- Identifier-to-analysis-request helpers for GNU Backgammon and BGSage.
- Renderer Position transport with separate semantic-state and view hashes.
- Deterministic renderer and JSON command-line interfaces.
- Packaged JSON schemas for the public position contracts.
- Mixed-license project policy: `AGPL-3.0-only` for software and `CC-BY-SA-4.0` for original explanatory and educational material.

### Changed

- Reworked public documentation for first-time package users, including clearer installation, quick-start, support-boundary, player-identity, and contribution guidance.

### Notes

- Version 0.3.0 intentionally supports a bounded set of source states and engine configurations. Unsupported or source-unknown facts remain explicit rather than being guessed.
- BGSage rollout and unverified engine settings are not supported in this release.

Release comparison links can be added after the corresponding public tag exists.
