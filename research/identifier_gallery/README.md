# Stable-player identifier reconciliation gallery

This research package produces the oracle-first XGID/GNUID reconciliation gallery discussed for Engine Kit.

For every retained edge case it shows both directions:

- **XGID → GNUID → XGID**
- **GNUID → XGID → GNUID**

The highlighted reference is released `backgammoncalculator` **v0.2.0** at immutable commit `a385a963ed01a6eac083dae7a1b246b1c150b3eb`. Alongside it, each direction is independently exercised through three method columns:

1. **Engine Kit native** identifier conversion.
2. **Engine Kit public API / bridge**.
3. **Direct AnkiGammon** conversion.

GNU Backgammon CLI is retained as a post-import and board-state diagnostic. Current `bglab::gnuid2xgid()` is a secondary GNUID → XGID diagnostic. Stable players are never swapped merely to make identifiers or pictures compare equal.

## Visual evidence contract

The three method columns intentionally preserve the earlier useful gallery structure. Within each method column:

1. GNUID evidence is rendered by the real GNU CLI at the top.
2. XGID evidence is rendered by `backgammonboard` underneath.
3. The canonical Engine Kit representation and field-level comparison follow underneath the two boards.

The renderer target is released `backgammonboard` **v0.1.1** at immutable commit `0bc70d30e458642f41d4976948e49492c2c6117c`. Gallery Board renders explicitly use `board_colors("bs")`, `board_style("bs")`, `player_name_style="checker"`, and the stable `player_1` display perspective. That perspective is presentation only and does not change canonical player identity.

The renderer lives in a dedicated gallery R library. Dependency installation requests the release tags; evidence records the requested ref and resolved commit. Verification accepts legitimate release provenance from `RemoteSha`, `RemoteRef`, `GithubSHA1`, or `GithubRef` and does not depend on `RemoteSha` alone.

Every complete GNUID used as primary evidence is loaded through the real GNU Backgammon CLI. Every XGID is rendered with the released Board public API. The reference also records a Board consumer diagnostic comparing direct complete-GNUID consumption with Calculator-to-XGID-to-Board consumption. Board remains a renderer/consumer, not a conversion authority. `bglab` remains explicitly secondary diagnostic evidence.

Primary classifications use exactly: `exact agreement`, `representational/default/normalization difference`, `factual state mismatch`, `unsupported/unavailable`, and `error`.

## Focused checker run

The launcher defaults to only `checker-4-2` and sends that fixture through the complete gallery and visual-evidence path. A different single fixture can be selected with `ORACLE_GALLERY_CASE_ID`.

## Focused run

From Git Bash at the Engine Kit repository root:

```bash
bash research/identifier_gallery/scripts/run_oracle_gallery.sh
```

The launcher installs/verifies Calculator v0.2.0 and Board v0.1.1 by requested release tag and resolved commit, refreshes or uses current bglab in `.r-library`, checks GNU CLI, runs the research unit suite, builds only the selected fixture, and opens the resulting HTML on Windows.

The Python entry point retains an unfiltered mode for later full-matrix work, but the focused launcher deliberately supplies `--case-id checker-4-2`.

Generated local libraries and artifacts are not committed.

## External held-out batch validation

The external runner accepts any CSV with `gnuid` and `xgid` columns. Additional
source columns are left untouched and are used for summary dimensions when
available. The supplied pair is diagnosed separately; each original identifier
is then tested independently in both directions through released Calculator
v0.2.0, Engine Kit native conversion, the public bridge, and Direct AnkiGammon.

From Git Bash at the repository root:

```bash
bash research/identifier_gallery/scripts/run_external_batch.sh /path/to/my-identifiers.csv
```

Calculator work is performed by one Rscript batch process, which emits immutable
release provenance and canonical factual states. Python streams that reference
file and writes one detail record per input row, direction, and surface. A
20,000-row input therefore normally produces 160,000 rows in
`roundtrip-results.csv`. Progress is printed every 1,000 rows by default; set
`EXTERNAL_BATCH_PROGRESS_INTERVAL` to change the interval.

Every run writes beneath `artifacts/external-identifier-batch-<timestamp>/`, then
the launcher automatically refreshes `SHA256SUMS.txt`, creates a ZIP and matching
`.sha256` file beneath `~/Documents/scratch/`, prints all paths and the ZIP hash,
opens that scratch directory in Explorer, and returns the original validation
status. Generated evidence remains untracked. GNU CLI, backgammonboard, board
rendering, and the full HTML oracle gallery are intentionally excluded from this
per-row path.

Hard failures are Calculator reference errors, Engine Kit native/public factual
mismatches, genuine Engine Kit native/public errors, and completed Engine Kit
native/public round trips that factually mismatch their own source. Explicit
public bridge `unsupported`/`unavailable` outcomes, source-pair disagreements,
normalization-only differences, and Direct AnkiGammon disagreements remain
visible evidence but do not by themselves make the validation exit nonzero.

## Outputs

```text
artifacts/oracle-identifier-comparison/
  oracle-gallery.html
  oracle-comparison-results.json
  method-comparisons.csv
  roundtrips.csv
  gnu-cli/
  renders/
```
