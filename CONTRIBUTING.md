# Contributing to Backgammon Engine Kit

Thank you for considering a contribution.

Backgammon Engine Kit sits at an integration boundary between identifier formats, canonical position state, engine analysis, caching, and rendering. Small semantic changes can affect several consumers, so contributions should be focused, explicit about compatibility, and supported by tests.

## Ways to contribute

Useful contributions include:

- reproducible bug reports;
- focused fixes for documented behavior;
- tests for supported identifier or position-state edge cases;
- improvements to error messages and validation;
- documentation corrections and examples;
- narrowly scoped support for additional engine configurations when accompanied by reproducible evidence.

For substantial API, schema, identifier-semantic, or engine-support changes, open an issue before implementing the change so the compatibility boundary can be discussed first.

## Development setup

Clone the repository and install it in editable mode:

```bash
python -m pip install -e .
python -m pytest
```

The package declares Python `>=3.8`. Release validation may run on a newer Python version, but contributions should avoid introducing syntax or standard-library requirements that exceed the declared minimum unless the project metadata is intentionally changed as part of the same proposal.

## Contribution principles

### Preserve stable player identity

Turn and display orientation must not change factual player ownership.

Universal Position and the native codec use stable `player_0` / `player_1` identities. The identifier analysis bridge retains its public `player_x` / `player_o` compatibility surface. A contribution must not infer identity by simply placing the player on roll at a preferred top/bottom position.

### Keep semantic state separate from presentation

Renderer view choices are presentation state. They must not alter checker ownership, cube ownership, score, turn, or semantic hashes.

### Keep unknown state explicit

Do not replace source-unknown facts with convenient defaults unless a public contract explicitly defines a normalization. If a downstream operation requires unavailable state, prefer a structured unavailable/unsupported result or a clear validation error.

### Make lossy conversion opt-in

When a source format contains a fact that the target format cannot represent, strict conversion should fail unless the caller explicitly opts into a documented loss. Loss and normalization records are part of the public behavior and should be tested.

### Do not broaden engine support by inference

An engine adapter does not imply that every engine setting is supported. New configuration claims should be backed by reproducible evidence and tests that prove the emitted/parsing behavior being exposed.

## Tests

Run the full Python suite before opening a pull request:

```bash
python -m pytest
```

Add focused regression coverage for behavior changes. Tests should cover factual state, not only exact identifier spelling, when multiple serialized forms can represent equivalent state.

For packaging or public-API changes, also build and inspect the distributions when the conventional release tooling is available:

```bash
python -m build
python -m twine check dist/*
```

Do not commit generated build output, virtual environments, caches, local evidence, credentials, or machine-specific files.

## Documentation

Public documentation should:

- explain user-visible behavior before implementation history;
- distinguish supported behavior from planned or unsupported behavior;
- use the public names defined by the API surface being documented;
- include executable examples when practical;
- avoid claims that depend on private evidence or local machine state;
- keep architecture/reference material precise without requiring knowledge of internal project milestones.

Because `README.md` is used as the package long description, changes to it should render cleanly on both GitHub and PyPI.

## Pull requests

A good pull request should contain:

1. a concise explanation of the user-visible problem;
2. the smallest coherent implementation that fixes it;
3. tests or evidence appropriate to the changed contract;
4. documentation updates when public behavior changes;
5. a clear statement of any compatibility, normalization, or representational-loss impact.

Avoid combining unrelated cleanup with a semantic change.

## Licensing

By contributing, you agree that your contribution is provided under the license applicable to the part of the repository you change, as described in [`LICENSE.md`](LICENSE.md):

- software contributions: `AGPL-3.0-only`;
- original explanatory and educational material: `CC-BY-SA-4.0`.

Third-party material must retain its original notices and licensing terms.
