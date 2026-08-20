# Sage 4/3 versus GNU 3/2 campaign v1

`campaign.json` is the sole experiment-defining authority for the ten-pair, twenty-match campaign. It freezes seven-point matches, mirrored A/B physical seats, Sage checker/cube 4ply/3ply, GNU checker/cube 3ply/2ply, GNU `normal-v1`, deterministic pair/seed identity, and the public Engine Kit release authority.

Engine Kit runtime authority is **v0.4.0**. The package source used to build the release wheel is `f87c69b10efa707f52aa1e42c74808d9b3bc109f`; the public tag target/release-proof commit is `f13446140ea06f9dc1ef51d4b6b0c83c5a46237d`. The exact wheel SHA-256 and production dependency lock SHA-256 are pinned by `campaign.json`.

Both Sage and GNU use `threads=1`. GNU checker `3ply` is a configured target; the Normal move filter may leave the recommended move or another candidate at a shallower actual depth. Candidate actual depths are retained as evidence. GNU cube actual depth must equal 2ply. Sage checker/cube top-level actual depth must equal the requested 4ply/3ply setting.

For a pending normal double, responder policy compares normalized `double-take` and `double-pass` doubler equities and fails closed on absent, non-numeric, tied, beaver, or raccoon data.

## Public release-backed environment

No Engine Kit source checkout is used for production execution. From the public campaign branch root, bootstrap the campaign-owned runner environment with the public `v0.4.0` release wheel and the committed hash-locked dependencies:

```bash
python3 -m runner.sage_gnu_campaign bootstrap \
  --runtime-root /operator/selected/benchmarker-runtime \
  --artifact-root /operator/selected/benchmarker-artifacts
```

Bootstrap performs, in order:

1. verify the committed dependency-lock hash against `campaign.json`;
2. download the exact wheel from the public `v0.4.0` tag and verify its pinned SHA-256;
3. create `{campaign_id}/runner-workspace/.venv`;
4. `pip install --require-hashes` from the committed production lock;
5. install the exact Engine Kit v0.4.0 wheel with `--no-deps`;
6. run `pip check`;
7. record Python, package RECORD, dependency freeze, wheel, lock, package-source, release-commit, and configuration identities.

Use the resulting campaign Python for preflight and execution:

```bash
/operator/selected/benchmarker-runtime/sage4-gnu3-7pt-mirrored-v1/runner-workspace/.venv/bin/python \
  -m runner.sage_gnu_campaign preflight \
  --runtime-root /operator/selected/benchmarker-runtime \
  --artifact-root /operator/selected/benchmarker-artifacts
```

A separately authorized bounded smoke pair uses:

```bash
/operator/selected/benchmarker-runtime/sage4-gnu3-7pt-mirrored-v1/runner-workspace/.venv/bin/python \
  -m runner.sage_gnu_campaign run \
  --runtime-root /operator/selected/benchmarker-runtime \
  --artifact-root /operator/selected/benchmarker-artifacts \
  --max-new-pairs 1 \
  --authorize-real-match
```

`STOP_AFTER_PAIR` requests a stop after the active pair is immutably published. No command in this runner performs post-match GNU analysis, corpus ingestion, Canonical writing, or Explainer work.
