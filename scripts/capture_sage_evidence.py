#!/usr/bin/env python3
"""Capture exactly one bounded BGSage analysis into a public-safe bundle."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from backgammon_engine_kit.models import AnalysisRequest, Position
from backgammon_engine_kit.sage import SageAdapter, SageRuntimeConfiguration, verified_sage_configuration
from backgammon_engine_kit.sage.config import SAGE_ENGINE_VERSION
from backgammon_engine_kit.serialization import canonical_json


ROOT = Path(__file__).resolve().parents[1]
POSITIONS = {
    "checker": {
        "position": "4PPgASTgc/ABMA:cAnqAAAAAAAE",
        "dice": (4, 2),
        "context": {
            "beaver": False,
            "crawford": False,
            "cube_owner": "centered",
            "cube_value": 1,
            "jacoby": False,
            "match_length": 7,
            "on_roll": "X",
            "opponent_score": 0,
            "player_score": 0,
        },
    },
    "cube": {
        "position": "bD3BAQyYd2cEAA:cAngAAAAAAAE",
        "dice": None,
        "context": {
            "beaver": False,
            "crawford": False,
            "cube_owner": "centered",
            "cube_value": 1,
            "jacoby": False,
            "match_length": 7,
            "on_roll": "X",
            "opponent_score": 0,
            "player_score": 0,
        },
    },
}
TIMEOUT_SECONDS = 120.0


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def write_text(path, value):
    path.write_text(value, encoding="utf-8")


def checksums(bundle):
    names = sorted(path.name for path in bundle.iterdir() if path.is_file() and path.name != "checksums.sha256")
    return "".join("{}  {}\n".format(sha256_bytes((bundle / name).read_bytes()), name) for name in names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("decision_type", choices=("checker", "cube"))
    args = parser.parse_args()
    spec = POSITIONS[args.decision_type]
    bundle = ROOT / "evidence" / "sage" / SAGE_ENGINE_VERSION / (args.decision_type + "-1ply")
    if bundle.exists():
        raise SystemExit("refusing to overwrite an existing Sage evidence bundle")

    request = AnalysisRequest(
        position=Position(id=spec["position"], format="gnuid"),
        engine="sage",
        analysis_setting="1ply",
        decision_type=args.decision_type,
        dice=spec["dice"],
        configuration=verified_sage_configuration(),
    )
    runtime = SageRuntimeConfiguration.discover()
    adapter = SageAdapter(runtime)
    record = adapter.run_verified(request, TIMEOUT_SECONDS)
    bundle.mkdir(parents=True)

    request_text = canonical_json(request.to_dict()) + "\n"
    result_text = canonical_json(record.result.to_dict()) + "\n"
    stdin_bytes = record.invocation.stdin_text.encode("utf-8")
    stdout_bytes = record.outcome.stdout.encode("utf-8")
    stderr_bytes = record.outcome.stderr.encode("utf-8")
    execution = {
        "completed_at": record.completed_at,
        "exit_status": record.outcome.returncode,
        "failure_code": record.outcome.failure_code,
        "identity_protocol_input": {
            "operation": "identify",
            "protocol": "bgsage-position-analysis-v1",
        },
        "invocation": record.invocation.public_argv(),
        "shell": False,
        "started_at": record.started_at,
        "stderr_sha256": sha256_bytes(stderr_bytes),
        "stdin_sha256": sha256_bytes(stdin_bytes),
        "stdout_sha256": sha256_bytes(stdout_bytes),
        "timeout_seconds": TIMEOUT_SECONDS,
    }
    configuration = {
        "analyzer": json.loads(record.invocation.stdin_text)["analysis"],
        "configuration_hash": request.configuration.configuration_hash,
        "environment": {
            "BGBOT_MULTIPLY_THREADS": "1",
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONHASHSEED": "0",
        },
        "public_runtime_identity": runtime.public_identity(),
        "request_configuration": request.configuration.to_dict(),
    }
    position_id, match_id = spec["position"].split(":", 1)
    source = {
        "accepted_source": {
            "context": spec["context"],
            "decision_type": args.decision_type,
            "dice": list(spec["dice"]) if spec["dice"] is not None else None,
            "match_id": match_id,
            "position_id": position_id,
            "provenance": "accepted GNU evidence-adapter milestone source position",
            "validation_status": "pass",
        },
        "conversion": {
            "method": "lossless GNU Position-ID bytes to BGSage native board decoder",
            "normalized_input": json.loads(record.outcome.stdout)["normalized_input"],
            "player_perspective": "player on roll; positive checkers are that player",
        },
    }
    readme = """# BGSage {kind} 1-ply evidence

This public-safe bundle records one fresh-process BGSage {version} single-position
{kind} analysis. The source GNU Position ID and Match ID come from the accepted
GNU evidence milestone. The position bytes are decoded with BGSage's native
decoder; match, cube, turn, score, Crawford, and dice context are decoded from
the accepted Match ID and checked before analysis.

The shell-free public invocation is
`["<BGSAGE_PYTHON>", "<ENGINE_KIT_SAGE_PROTOCOL>"]`. `stdin.json` is the complete canonical protocol
request and `stdout.json` is the immutable response. Runtime paths are replaced
by content identities. The analyzer is explicitly 1-ply, cubeful, one-threaded,
stage9, seed 42, with bundled bearoff data. Candidate filters, noise, and pruning
are not applicable/exposed for this 1-ply engine path. No rollout was run.

`checksums.sha256` covers every other file in this directory. The parser version
and configuration identity are in `request.json` and `configuration.json`.
""".format(kind=args.decision_type, version=SAGE_ENGINE_VERSION)

    write_text(bundle / "request.json", request_text)
    write_text(bundle / "normalized-result.json", result_text)
    write_text(bundle / "stdin.json", record.invocation.stdin_text)
    write_text(bundle / "stdout.json", record.outcome.stdout)
    write_text(bundle / "stderr.txt", record.outcome.stderr)
    write_text(bundle / "execution.json", canonical_json(execution) + "\n")
    write_text(bundle / "configuration.json", canonical_json(configuration) + "\n")
    write_text(bundle / "source.json", canonical_json(source) + "\n")
    write_text(bundle / "README.md", readme)
    write_text(bundle / "checksums.sha256", checksums(bundle))
    sys.stdout.write(
        canonical_json(
            {
                "bundle": str(bundle.relative_to(ROOT)),
                "decision_type": args.decision_type,
                "status": "complete",
                "stdout_sha256": execution["stdout_sha256"],
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
