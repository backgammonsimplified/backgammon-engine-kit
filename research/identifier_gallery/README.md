# Stable-player identifier reconciliation gallery

This research package produces the oracle-first XGID/GNUID reconciliation gallery discussed for Engine Kit.

For every retained edge case it shows both directions:

- **XGID → GNUID → XGID**
- **GNUID → XGID → GNUID**

The highlighted reference is `backgammoncalculator` **0.2.0**. Alongside it, each direction is independently exercised through three method columns:

1. **Native Python** identifier conversion in Engine Kit.
2. **Engine Kit public API / request bridge**.
3. **Direct AnkiGammon** conversion.

GNU Backgammon CLI is retained as a post-import and board-state diagnostic. Current `bglab::gnuid2xgid()` is a secondary GNUID → XGID diagnostic. Stable players are never swapped merely to make identifiers or pictures compare equal.

## Visual evidence contract

The three method columns intentionally preserve the earlier useful gallery structure. Within each method column:

1. GNUID evidence is rendered by the real GNU CLI at the top.
2. XGID evidence is rendered by `backgammonboard` underneath.
3. The canonical Engine Kit representation and field-level comparison follow underneath the two boards.

The current renderer target is `backgammonboard` master commit `0bc70d30e458642f41d4976948e49492c2c6117c`, package version `0.1.1`. Gallery Board renders explicitly use `board_colors("bs")`, `board_style("bs")`, `player_name_style="checker"`, and the stable `player_1` display perspective. That perspective is presentation only and does not change canonical player identity.

The renderer lives in a dedicated gallery R library. Provenance accepts the immutable requested full commit from either DESCRIPTION `RemoteSha` or `RemoteRef`, because `remotes::install_github()` does not reliably populate `RemoteSha` for every archive installation.

## Focused checker run

During parity recovery, do not start with the full fixture matrix. The task-management runner extracts only `checker-4-2` and sends that one fixture through this same gallery code and visual layout.

## Full run

From Git Bash at the Engine Kit repository root:

```bash
bash research/identifier_gallery/scripts/run_oracle_gallery.sh
```

The launcher verifies Calculator 0.2.0 and its release commit, installs the pinned current `backgammonboard` source into `.renderer-library`, refreshes or uses current bglab in `.r-library`, checks GNU CLI, runs the research unit suite, builds the gallery, and opens the resulting HTML on Windows.

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
