#!/usr/bin/env python3
"""Capture exactly one selected, provenance-verified GNU position analysis."""

import argparse
import hashlib
import json
from pathlib import Path

from backgammon_engine_kit.gnu import GnuAdapter, GnuRuntimeConfiguration, verified_gnu_configuration
from backgammon_engine_kit.models import AnalysisRequest, Position, RawSource
from backgammon_engine_kit.serialization import canonical_json, ensure_public_safe


ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 30.0
SOURCE_EXPORT_SHA256 = "aa3a63cb8adc24d4b372bc7f086a0642626420b5b78ec2b92e3fc248a5be09a2"
SOURCE_VALIDATION_SHA256 = "2cdd553d9cafcc652f8068a84c8415d3741c060c1b2f73567da4dafd35dfea64"
SOURCE_MATCH_SHA256 = "9a067d4bdedbc36c4a8c1ffa24300e0b08115a77fd6d009a8a54ceaaec30f7a1"

CASES = {
    "checker": {
        "bundle": "checker-1ply",
        "gnuid": "4PPgASTgc/ABMA:cAnqAAAAAAAE",
        "xgid": "XGID=-a-a--E-C---dE---d-e----B-:0:0:1:42:0:0:0:0:8",
        "dice": (4, 2),
        "decision": 2,
        "player_on_roll": "player1",
    },
    "cube": {
        "bundle": "cube-1ply",
        "gnuid": "bD3BAQyYd2cEAA:cAngAAAAAAAE",
        "xgid": "XGID=---bB-DCC-B-cA---a-dabb---:0:0:1:00:0:0:0:0:8",
        "dice": None,
        "decision": 10,
        "player_on_roll": "player1",
    },
}


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    ensure_public_safe(value, path.name)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def request_for(decision_type):
    case = CASES[decision_type]
    return AnalysisRequest(
        position=Position(id=case["gnuid"], format="gnuid"),
        engine="gnu",
        analysis_setting="1ply",
        decision_type=decision_type,
        dice=case["dice"],
        configuration=verified_gnu_configuration(),
    )


def source_record(decision_type):
    case = CASES[decision_type]
    position_id, match_id = case["gnuid"].split(":", 1)
    return {
        "schema_version": "gnu-evidence-source-v1",
        "accepted_source": {
            "artifact": "pair09-benchmark-decisions.json",
            "artifact_sha256": SOURCE_EXPORT_SHA256,
            "validation_status": "pass",
            "validation_sha256": SOURCE_VALIDATION_SHA256,
            "source_match_sha256": SOURCE_MATCH_SHA256,
            "match_side": "A",
            "game_number": 1,
            "decision_number": case["decision"],
        },
        "position": {
            "gnu_position_id": position_id,
            "gnu_match_id": match_id,
            "xgid": case["xgid"],
            "dice": list(case["dice"]) if case["dice"] is not None else None,
            "player_on_roll": case["player_on_roll"],
            "match_length": 7,
            "scores": {"player0": 0, "player1": 0},
            "cube_value": 1,
            "cube_owner": "centered",
            "crawford": False,
            "jacoby": False,
            "beavers": 0,
        },
    }


def manifest(request, runtime, execution):
    return {
        "schema_version": "gnu-evidence-execution-v1",
        "engine": {
            "name": "gnu",
            "version": request.configuration.engine_version,
            "executable": runtime.public_identity()["executable"],
            "neural_networks": [
                {"name": "contact", "version": "1.01", "inputs": 250, "hidden_units": 128},
                {"name": "crashed", "version": "1.01", "inputs": 250, "hidden_units": 128},
                {"name": "race", "version": "1.01", "inputs": 214, "hidden_units": 128},
            ],
            "resources": runtime.public_identity()["resources"],
            "bearoff": {
                "one_sided": "15 checkers on 6 points; gammon distributions",
                "two_sided": "6 checkers on 6 points; cubeful and cubeless equities",
            },
        },
        "configuration": request.configuration.to_dict(),
        "requested_analysis_setting": request.analysis_setting,
        "requested_decision_type": request.decision_type,
        "invocation": {
            "argv": execution.invocation.public_argv(),
            "environment": {
                "HOME": "isolated-nonpersistent",
                "LANG": "C",
                "LC_ALL": "C",
                "OMP_NUM_THREADS": "1",
            },
            "stdin_file": "stdin.gnubg",
            "shell": False,
        },
        "started_at": execution.started_at,
        "completed_at": execution.completed_at,
        "timeout_seconds": TIMEOUT_SECONDS,
        "exit_status": execution.outcome.returncode,
        "process_status": execution.outcome.status,
        "failure_code": execution.outcome.failure_code,
        "stdout_sha256": sha256_bytes(execution.outcome.stdout.encode("utf-8")),
        "stderr_sha256": sha256_bytes(execution.outcome.stderr.encode("utf-8")),
        "parser_version": request.configuration.parser_version,
    }


def write_checksums(bundle):
    entries = []
    for path in sorted(bundle.iterdir(), key=lambda value: value.name):
        if path.is_file() and path.name != "checksums.sha256":
            entries.append("{}  {}".format(sha256_bytes(path.read_bytes()), path.name))
    (bundle / "checksums.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")


def provenance_text(decision_type, execution):
    case = CASES[decision_type]
    position_id, match_id = case["gnuid"].split(":", 1)
    return """# GNU {kind} 1-ply evidence

This public-safe bundle is a single fresh GNU Backgammon 1.08.003 scripted
position analysis. Its source position is decision {decision} from an accepted,
validation-passing Stage 1 match artifact. The source artifact, validation
record, and source match are identified by SHA-256 in `source.json` without
copying benchmark orchestration or private paths.

- GNU Position ID: `{position_id}`
- GNU Match ID: `{match_id}`
- Requested setting: `1ply`
- Actual parser: `gnu-text-parser-v1`
- Start: `{started}`
- Completion: `{completed}`
- Exit status: `{exit_status}`
- Timeout: 30 seconds

`stdin.gnubg`, `stdout.txt`, and `stderr.txt` are the immutable process streams.
`execution.json` records the sanitized argv/environment, verified executable,
network, weights, bearoff and match-equity identities, settings, timestamps,
and stream checksums. `normalized-result.json` contains no derived engine
measurements: omitted values remain explicit nulls. `checksums.sha256` covers
every other file in this bundle.
""".format(
        kind=decision_type,
        decision=case["decision"],
        position_id=position_id,
        match_id=match_id,
        started=execution.started_at,
        completed=execution.completed_at,
        exit_status=execution.outcome.returncode,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("decision_type", choices=sorted(CASES))
    args = parser.parse_args()
    case = CASES[args.decision_type]
    bundle = ROOT / "evidence" / "gnu" / "1.08.003" / case["bundle"]
    if bundle.exists():
        raise SystemExit("refusing to replace an existing immutable evidence bundle")
    bundle.mkdir(parents=True)

    request = request_for(args.decision_type)
    runtime = GnuRuntimeConfiguration.discover()
    adapter = GnuAdapter(runtime)
    execution = adapter.execute(request, TIMEOUT_SECONDS)

    # Persist the exact streams before parsing so a parser repair never requires
    # another live engine analysis.
    (bundle / "stdin.gnubg").write_text(execution.invocation.stdin_text, encoding="utf-8")
    (bundle / "stdout.txt").write_text(execution.outcome.stdout, encoding="utf-8")
    (bundle / "stderr.txt").write_text(execution.outcome.stderr, encoding="utf-8")
    write_json(bundle / "request.json", request.to_dict())
    write_json(bundle / "source.json", source_record(args.decision_type))
    write_json(bundle / "execution.json", manifest(request, runtime, execution))

    if execution.outcome.status != "complete":
        write_checksums(bundle)
        raise SystemExit("GNU process did not complete successfully")
    raw = RawSource.from_output(execution.outcome.stdout, captured_at=execution.completed_at)
    result = adapter.parser.parse(
        request,
        raw,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
    )
    write_json(bundle / "normalized-result.json", result.to_dict())
    (bundle / "README.md").write_text(
        provenance_text(args.decision_type, execution), encoding="utf-8"
    )
    write_checksums(bundle)


if __name__ == "__main__":
    main()
