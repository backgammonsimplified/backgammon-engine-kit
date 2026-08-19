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

## Evidence gate

Configuration is composable, but support remains evidence-gated by decision type. The current retained evidence supports:

```text
Sage checker: 1ply, 4ply
Sage cube:    1ply, 3ply
GNU checker:  1ply, 3ply
GNU cube:     1ply, 2ply
```

An unsupported decision/depth combination is rejected before engine execution. Adding another setting requires evidence and a capability update, not a Benchmarker-local workaround.

For every accepted request, the adapter also verifies the engine-reported actual evaluation depth. A request fails closed if actual depth differs from the pinned request.

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

Engine Kit should therefore remain reusable across benchmark campaigns. Benchmarker chooses and freezes an Engine Kit profile; Engine Kit translates that profile into exact engine commands/protocol requests and verifies the resulting engine output.
