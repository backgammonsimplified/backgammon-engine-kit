# Configurable engine profiles

Backgammon Engine Kit owns engine-facing configuration identity, invocation construction, runtime identity verification, parsing, and fail-closed verification of what the engine actually ran.

It does not own benchmark campaign orchestration.

## Historical Sage-vs-GNU profile

The retained 20-match Sage-vs-GNU trial used these decision-specific settings:

```text
Sage checker: 4ply
Sage cube:    3ply
GNU checker:  3ply
GNU cube:     2ply
```

Authority: `backgammonsimplified/backgammon_bot@336d7eca593cdf08461c917cbc213b8c4cee4668`, specifically `referee.py` and `scripts/referee_mirrored_pair.py`.

The Engine Kit configuration API represents checker and cube depth independently so a consumer does not need to copy or patch engine command construction.

```python
from backgammon_engine_kit.gnu import gnu_configuration
from backgammon_engine_kit.sage import sage_configuration

sage = sage_configuration(checker_setting="4ply", cube_setting="3ply")
gnu = gnu_configuration(checker_plies=3, cube_plies=2)
```

The original `verified_sage_configuration()` and `verified_gnu_configuration()` remain the exact v0.3.0 1-ply configurations for compatibility.

## Runtime configuration versus retained evidence

Numeric 1-ply through 4-ply checker and cube settings are independently configurable for Sage and GNU. A new benchmark can therefore pin a different numeric checker/cube combination without requiring another Engine Kit source change.

Configuration does not imply that a setting has already passed a project commissioning smoke. `capability_report().supports(...)` remains a conservative retained-evidence signal. Current retained evidence includes:

```text
Sage checker: 1ply, 4ply
Sage cube:    1ply, 3ply
GNU checker:  1ply, 3ply
GNU cube:     1ply, 2ply
```

Other numeric 1-4 ply combinations may be configured for a bounded commissioning run. They are not treated as previously verified merely because they can be represented.

## GNU checker depth and move filters

GNU checker `plies` configure the target search depth. They do not guarantee that every legal move, or even the recommended move, will be evaluated to that target depth when move filters are enabled.

The historical trial changed GNU checker/cube plies but did not override GNU's move filters. With the pinned GNU 1.08.003 executable this means the Normal move-filter profile:

```text
1 ply:  0-ply -> accept 0, extra 8 within 0.160
2 ply:  0-ply -> accept 0, extra 8 within 0.160
        1-ply -> no pruning
3 ply:  0-ply -> accept 0, extra 8 within 0.160
        1-ply -> no pruning
        2-ply -> accept 0, extra 2 within 0.040
4 ply:  0-ply -> accept 0, extra 8 within 0.160
        1-ply -> no pruning
        2-ply -> accept 0, extra 2 within 0.040
        3-ply -> no pruning
```

Configurable GNU profiles encode this complete Normal filter as part of their public configuration identity and explicitly send all filter rows to the engine. The parser verifies the configured checker depth, cube depth, thread count, and Normal filter from GNU's own `show evaluation` output.

For GNU checker results, `AnalysisResult.analysis_setting` is the configured checker target and each `CheckerCandidate.actual_ply` is the depth GNU actually used for that move after filtering. `CheckerDecision.actual_ply` is therefore the emitted depth of the recommended move and may be lower than the configured checker target. A candidate deeper than the configured target is rejected.

GNU cube analysis does not use the checker move-filter mechanism, so cube `actual_ply` must equal the configured cube depth.

For Sage checker/cube analysis, the top-level emitted evaluation depth must equal the requested setting. Sage may still retain lower-ply filtered checker candidates; those candidate depths are preserved rather than relabeled.

Runtime binary/model/resource identity must also match the pinned Engine Kit authority. Settings outside the currently supported numeric 1-4 ply surface remain rejected.

## Ownership boundary with Benchmarker

The intended integration is:

```text
Benchmarker experiment configuration
  -> pins Engine Kit version and engine profile
  -> pins campaign identity, seeds, pair order, stopping/checkpoint rules
  -> creates isolated runner workspaces

Runner workspace
  -> contains the runtime environment and verified engine binaries/resources
  -> executes the pinned Benchmarker task using Engine Kit adapters/contracts
  -> is not the authority for experiment settings

Artifact repository/root
  -> receives immutable run outputs, manifests, logs, checksums, and provenance
  -> remains separate from Engine Kit source and disposable runner environments
```

Engine Kit should therefore remain reusable across benchmark campaigns. Benchmarker chooses and freezes an Engine Kit profile; Engine Kit translates that profile into exact engine commands/protocol requests and verifies the resulting engine output. Runtime paths are execution details and must not become part of the public configuration identity.
