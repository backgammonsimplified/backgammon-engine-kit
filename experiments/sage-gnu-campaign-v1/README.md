# Sage 4/3 versus GNU 3/2 campaign v1

`campaign.json` is the sole experiment-defining authority for the commissioned
ten-pair, twenty-match campaign. It freezes seven-point matches, mirrored A/B
physical seats, Sage checker/cube targets 4ply/3ply, GNU checker/cube targets
3ply/2ply, GNU's complete `normal-v1` move filter, deterministic pair/seed
identity, and Engine Kit source commit
`833929ea72ccec058527f3cd1fa0b54a07ac666b`.

Both Sage and GNU are deliberately pinned to `threads=1`. This is a
conservative, reproducible commissioning setting, not a claim of historical
parallelism or throughput equivalence. The historical equivalence claim is
limited to the validated decision-depth/filter profile and mirrored
physical-seat dice protocol. Any later requirement for historical automatic
logical-core parallelism is new campaign authority and must change the
configuration hash and pair identities.

GNU checker `3ply` is a configured target. The Normal filter may leave the
recommended move or another candidate at a shallower actual depth. Engine Kit
verifies GNU's configured checker/cube/filter output and the runner records both
the target and every observed candidate depth in `matches/{A,B}/decisions.jsonl`.
It never asserts that GNU's recommended actual depth equals three.

For a pending normal double, the responder policy ignores the overall
doubler/on-roll recommendation. It compares Engine Kit's normalized
`double-take` and `double-pass` equities and chooses the result with lower
doubler equity, failing closed if either action is absent or ambiguous. Beaver
and raccoon responses are not enabled.

## Historical dice authority

The physical-seat implementation was recovered from
`backgammonsimplified/backgammon_bot@336d7eca593cdf08461c917cbc213b8c4cee4668`:

- `scripts/referee_mirrored_pair.py`: `run_match` creates independent
  `<base>:match:A` and `<base>:match:B` namespace seeds and reverses Sage from O
  to X.
- `scripts/seat_dice_streams.py`: `dice_record`,
  `SeatDiceController._opening_dice`, `_checker_dice`, `dice_for_prompt`, and
  `prepare_after_turn` define the random key, opening consumption, physical-seat
  checker consumption, and next-seat behavior.
- `referee.py`: `PromptAwareGnubg.send_command`, `set_match_rng_file`, and
  `run_matches` establish manual-prompt consumption and per-side independence.

A and B deliberately do not share consumption counters. Once play diverges,
each namespace continues independently without forced roll synchronization.

## Ownership and output

Benchmarker owns campaign/pair identity, dice, workspaces, ledger transitions,
stopping, immutable publication, manifests, and checksums. Engine Kit alone owns
Sage/GNU decision configuration, invocation, runtime verification, and parsing.
The pinned GNU executable is also used as a neutral two-human board/rules process;
its evaluator is never used for a campaign move through that process.

Operator-selected runtime and artifact roots are mandatory and must be separate,
outside both source checkouts, and outside denied cross-campaign path components.
Incomplete attempts remain in the runtime root and restart from the beginning with
the same pair ID, base seed, and A/B stream identities. A committed pair is
verified and skipped; any authority or checksum conflict stops the campaign.
Campaign/run/pair JSON, checksum files, and commit markers use same-directory
atomic replacement with file and directory `fsync`; completed staging trees are
flushed before their one-way publication into immutable pair destinations.

## Operator commands

From the Benchmarker root, create the campaign-owned runner environment once.
Bootstrap takes a `git archive` of the exact clean Engine Kit source commit,
builds and installs its wheel non-editably, and records the source archive,
wheel, installed distribution, Python, and dependency-freeze identities beneath
the selected runtime root:

```bash
.venv/bin/python -m runner.sage_gnu_campaign bootstrap \
  --engine-kit-root ../engine-kit-configurable-profiles \
  --runtime-root /operator/selected/benchmarker-runtime \
  --artifact-root /operator/selected/benchmarker-artifacts
```

Bootstrap is idempotent only when every recorded identity still reconciles. An
incomplete or conflicting existing environment is preserved and rejected for
operator review; it is never silently mutated. The Engine Kit checkout remains
an immutable source input and its `.venv` is not used to run matches.

Use the resulting campaign Python for read-only preflight or real execution:

```bash
/operator/selected/benchmarker-runtime/sage4-gnu3-7pt-mirrored-v1/runner-workspace/.venv/bin/python \
  -m runner.sage_gnu_campaign plan

/operator/selected/benchmarker-runtime/sage4-gnu3-7pt-mirrored-v1/runner-workspace/.venv/bin/python \
  -m runner.sage_gnu_campaign preflight \
  --engine-kit-root ../engine-kit-configurable-profiles \
  --runtime-root /operator/selected/benchmarker-runtime \
  --artifact-root /operator/selected/benchmarker-artifacts
```

After the fresh no-contention preflight and separate operator authorization, one
bounded smoke pair is:

```bash
/operator/selected/benchmarker-runtime/sage4-gnu3-7pt-mirrored-v1/runner-workspace/.venv/bin/python \
  -m runner.sage_gnu_campaign run \
  --engine-kit-root ../engine-kit-configurable-profiles \
  --runtime-root /operator/selected/benchmarker-runtime \
  --artifact-root /operator/selected/benchmarker-artifacts \
  --max-new-pairs 1 \
  --authorize-real-match
```

Omit `--max-new-pairs` only when authorizing the remaining committed campaign
bound. `STOP_AFTER_PAIR` under the durable campaign root requests a stop after
the active pair has been immutably published. No command in this runner performs
post-match GNU analysis, corpus ingestion, or Explainer work.
