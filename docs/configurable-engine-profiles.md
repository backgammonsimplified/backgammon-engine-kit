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

For every real request, the adapter verifies the engine-reported actual evaluation depth. A result fails closed if actual depth differs from the pinned request, or if runtime binary/model/resource identity differs from the pinned Engine Kit authority. Settings outside the currently supported numeric 1-4 ply surface remain rejected.

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
