# GNU checker 1-ply evidence

This public-safe bundle is a single fresh GNU Backgammon 1.08.003 scripted
position analysis. Its source position is decision 2 from an accepted,
validation-passing Stage 1 match artifact. The source artifact, validation
record, and source match are identified by SHA-256 in `source.json` without
copying benchmark orchestration or private paths.

- GNU Position ID: `4PPgASTgc/ABMA`
- GNU Match ID: `cAnqAAAAAAAE`
- Requested setting: `1ply`
- Actual parser: `gnu-text-parser-v1`
- Start: `2026-07-22T20:28:34.368938Z`
- Completion: `2026-07-22T20:28:34.464039Z`
- Exit status: `0`
- Timeout: 30 seconds

`stdin.gnubg`, `stdout.txt`, and `stderr.txt` are the immutable process streams.
`execution.json` records the sanitized argv/environment, verified executable,
network, weights, bearoff and match-equity identities, settings, timestamps,
and stream checksums. `normalized-result.json` contains no derived engine
measurements: omitted values remain explicit nulls. `checksums.sha256` covers
every other file in this bundle.
