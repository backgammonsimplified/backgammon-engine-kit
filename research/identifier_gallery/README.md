# Stable-player identifier reconciliation gallery

This research package produces the full oracle-first XGID/GNUID reconciliation gallery discussed for Engine Kit.

For every retained edge case it shows both directions:

- **XGID → GNUID → XGID**
- **GNUID → XGID → GNUID**

The highlighted reference is released `backgammoncalculator` **0.2.0**. Alongside it, each direction is independently exercised through:

1. **Native Python** identifier conversion in Engine Kit.
2. **Engine Kit public API / request bridge**.
3. **Direct AnkiGammon** conversion.

GNU Backgammon CLI is retained as a post-import / board-state diagnostic, not the canonical conversion oracle. Current `bglab::gnuid2xgid()` is a secondary GNUID → XGID diagnostic. Stable players are never swapped merely to make identifiers compare equal.

## Visual evidence

Every available XGID endpoint is rendered with `backgammonboard` from the exact GitHub source commit pinned by the launcher. The renderer lives in a dedicated gallery R library so an older installed package with the same `0.1.0` version cannot be selected accidentally. Renderer provenance records the exact `RemoteSha`.

GNUID states are rendered through GNU CLI text-board evidence. Canonical Engine Kit state is shown with factual and metadata diffs so identifier equality is not used as a substitute for semantic comparison.

## Run

From Git Bash at the Engine Kit repository root:

```bash
bash research/identifier_gallery/scripts/run_oracle_gallery.sh
```

The launcher verifies Calculator 0.2.0 and its release commit, installs the pinned current `backgammonboard` source into `.renderer-library`, refreshes/uses current bglab in `.r-library`, checks GNU CLI, runs the full research unit suite, builds the gallery, and opens the resulting HTML on Windows.

Generated local libraries and artifacts are not committed.

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
