# BGSage cube 1-ply evidence

This public-safe bundle records one fresh-process BGSage 1.2.20260706 single-position
cube analysis. The source GNU Position ID and Match ID come from the accepted
GNU evidence milestone. The position bytes are decoded with BGSage's native
decoder; match, cube, turn, score, Crawford, and dice context are decoded from
the accepted Match ID and checked before analysis.

The shell-free public invocation is
`["<BGSAGE_PYTHON>", "<ENGINE_KIT_SAGE_PROTOCOL>"]`. `stdin.json` is the complete canonical protocol
request and `stdout.json` is the immutable response. Runtime paths are replaced
by content identities. The analyzer is explicitly 1-ply, cubeful, one-threaded,
stage9, seed 42, with bundled bearoff data. Candidate filters, noise, and pruning
are not applicable/exposed for this 1-ply engine path. No rollout was run.

`checksums.sha256` covers every other file in this directory. The parser version
and configuration identity are in `request.json` and `configuration.json`.
