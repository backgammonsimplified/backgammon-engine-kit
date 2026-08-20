"""Operator CLI: plan, preflight, status, or explicitly authorize execution."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .campaign import campaign_status, run_campaign
from .config import DEFAULT_CONFIG, load_campaign_config
from .identity import all_pair_identities
from .preflight import bootstrap, preflight


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate the frozen Sage 4/3 versus GNU 3/2 campaign.")
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    result.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="Print deterministic pair identities without creating state.")
    status = commands.add_parser("status", help="Read durable ledger status.")
    status.add_argument("--artifact-root", type=Path, required=True)
    for name in ("bootstrap", "preflight", "run"):
        command = commands.add_parser(name)
        command.add_argument("--engine-kit-root", type=Path, required=True)
        command.add_argument("--runtime-root", type=Path, required=True)
        command.add_argument("--artifact-root", type=Path, required=True)
        if name == "run":
            command.add_argument(
                "--authorize-real-match",
                action="store_true",
                help="Required explicit operator gate; this command performs real engine compute.",
            )
            command.add_argument(
                "--max-new-pairs",
                type=int,
                help="Operational stop bound only; does not alter campaign identity or settings.",
            )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_campaign_config(args.config)
    if args.command == "plan":
        output = {
            "campaign_id": config.campaign_id,
            "configuration_sha256": config.content_sha256,
            "pairs": [identity.to_dict() for identity in all_pair_identities(config)],
        }
    elif args.command == "status":
        output = campaign_status(config, args.artifact_root)
    elif args.command == "bootstrap":
        output = bootstrap(
            config,
            args.repository,
            args.engine_kit_root,
            args.runtime_root,
            args.artifact_root,
        )
    elif args.command == "preflight":
        output = preflight(
            config,
            args.repository,
            args.engine_kit_root,
            args.runtime_root,
            args.artifact_root,
            require_clean_benchmarker=True,
            load_engine_runtime=True,
        )
    else:
        if not args.authorize_real_match:
            parser().error("run requires --authorize-real-match after the separate operator gate")
        if args.max_new_pairs is not None and args.max_new_pairs <= 0:
            parser().error("--max-new-pairs must be positive")
        output = run_campaign(
            config,
            args.repository,
            args.engine_kit_root,
            args.runtime_root,
            args.artifact_root,
            [sys.executable, "-m", "runner.sage_gnu_campaign", *(argv or sys.argv[1:])],
            max_new_pairs=args.max_new_pairs,
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
