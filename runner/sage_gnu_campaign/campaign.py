"""Bounded, resumable, idempotent campaign orchestration and publication."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from .config import CampaignConfig
from .engine_kit import EngineKitSession
from .environment import pair_attempt_workspace, runner_workspace
from .identity import PairIdentity, all_pair_identities
from .ledger import CampaignLedger, utc_now
from .manifests import (
    checksum_entries,
    checksum_text,
    common_manifest,
    fsync_directory,
    fsync_tree,
    host_identity,
    path_identity,
    sanitized_launch_command,
    sha256_file,
    verify_checksum_file,
    write_bytes_atomic,
    write_json,
)
from .match import PairExecutor
from .preflight import preflight


COMMIT_MARKER_SCHEMA = "sage-gnu-pair-commit-v1"


class CampaignError(RuntimeError):
    """Campaign orchestration cannot continue without violating authority."""


def campaign_root(artifact_root: Path, config: CampaignConfig) -> Path:
    return Path(artifact_root).resolve() / config.campaign_id


def pair_root(artifact_root: Path, config: CampaignConfig, identity: PairIdentity) -> Path:
    return campaign_root(artifact_root, config) / "pairs" / identity.pair_id


def _marker_authority(
    config: CampaignConfig,
    identity: PairIdentity,
    benchmarker_commit: str,
    engine_kit_commit: str,
) -> dict[str, Any]:
    return {
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "pair_index": identity.pair_index,
        "pair_id": identity.pair_id,
        "base_seed": identity.base_seed,
        "benchmarker_commit": benchmarker_commit,
        "engine_kit_source_commit": engine_kit_commit,
    }


def verify_committed_pair(
    root: Path,
    config: CampaignConfig,
    identity: PairIdentity,
    benchmarker_commit: str,
    engine_kit_commit: str,
) -> str:
    root = Path(root)
    marker_path = root / "_COMMITTED.json"
    manifest_path = root / "pair_manifest.json"
    checksums_path = root / "checksums.sha256"
    if not marker_path.is_file() or not manifest_path.is_file() or not checksums_path.is_file():
        raise CampaignError(f"incomplete or conflicting committed pair directory: {root}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("schema_version") != COMMIT_MARKER_SCHEMA or marker.get("state") != "committed":
        raise CampaignError("pair commit marker schema/state mismatch")
    authority = _marker_authority(config, identity, benchmarker_commit, engine_kit_commit)
    conflicts = [key for key, value in authority.items() if marker.get(key) != value]
    if conflicts:
        raise CampaignError("committed pair authority mismatch: " + ", ".join(conflicts))
    if marker.get("pair_manifest_sha256") != sha256_file(manifest_path):
        raise CampaignError("committed pair manifest hash mismatch")
    if marker.get("checksums_sha256") != sha256_file(checksums_path):
        raise CampaignError("committed pair checksum-file hash mismatch")
    entries = verify_checksum_file(root, checksums_path)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"checksums.sha256", "_COMMITTED.json"}
    }
    if actual != set(entries):
        raise CampaignError("committed pair file inventory differs from checksum manifest")
    return sha256_file(marker_path)


def publish_pair(
    execution_root: Path,
    artifact_root: Path,
    config: CampaignConfig,
    identity: PairIdentity,
    common: dict[str, Any],
    ledger_pair: dict[str, Any],
) -> str:
    destination = pair_root(artifact_root, config, identity)
    if destination.exists():
        raise CampaignError(f"refusing to overwrite existing pair output: {destination}")
    staging = destination.parent / f".{identity.pair_id}.staging-attempt-{ledger_pair['attempt_count']}"
    if staging.exists():
        raise CampaignError(f"preserved publication staging directory requires review: {staging}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(execution_root, staging)
    execution_result = json.loads((staging / "execution_result.json").read_text(encoding="utf-8"))
    if execution_result.get("status") != "complete" or execution_result.get("pair_identity") != identity.to_dict():
        raise CampaignError("pair executor result does not match planned identity")
    output_hashes = checksum_entries(staging)
    finalized_at = utc_now()
    pair_manifest = {
        **common,
        "manifest_type": "pair",
        "pair_identity": identity.to_dict(),
        "match_length_points": config.data["match"]["length_points"],
        "matches": execution_result["matches"],
        "candidate_actual_depth_evidence": {
            side: f"matches/{side}/decisions.jsonl" for side in ("A", "B")
        },
        "physical_seat_stream_evidence": {
            side: f"matches/{side}/dice/seat_dice_manifest.json" for side in ("A", "B")
        },
        "runner_attempt": ledger_pair["attempt_count"],
        "runner_workspace_identity": path_identity(execution_root.parent, "pair-attempt-workspace"),
        "artifact_root_identity": path_identity(artifact_root, "durable-artifact-root"),
        "state": "committed",
        "state_transition_timestamps": [
            *ledger_pair["transitions"],
            {
                "from": "started",
                "to": "committed",
                "at_utc": finalized_at,
                "reason": "immutable-pair-publication",
                "attempt": ledger_pair["attempt_count"],
            },
        ],
        "output_file_sha256": output_hashes,
    }
    write_json(staging / "pair_manifest.json", pair_manifest)
    entries = checksum_entries(staging, excluded=("checksums.sha256", "_COMMITTED.json"))
    checksums_path = staging / "checksums.sha256"
    write_bytes_atomic(checksums_path, checksum_text(entries))
    marker = {
        "schema_version": COMMIT_MARKER_SCHEMA,
        "state": "committed",
        **_marker_authority(
            config,
            identity,
            common["benchmarker"]["commit"],
            common["engine_kit"]["source_commit"],
        ),
        "committed_at_utc": finalized_at,
        "pair_manifest_sha256": sha256_file(staging / "pair_manifest.json"),
        "checksums_sha256": sha256_file(checksums_path),
    }
    write_json(staging / "_COMMITTED.json", marker)
    fsync_tree(staging)
    os.replace(staging, destination)
    fsync_directory(destination.parent)
    return verify_committed_pair(
        destination,
        config,
        identity,
        common["benchmarker"]["commit"],
        common["engine_kit"]["source_commit"],
    )


def _initialize_campaign_manifest(
    root: Path,
    config: CampaignConfig,
    common: dict[str, Any],
    runtime_root: Path,
    artifact_root: Path,
    launch_command: str,
) -> Path:
    path = root / "campaign_manifest.json"
    planned = [identity.to_dict() for identity in all_pair_identities(config)]
    expected_authority = {
        "campaign_id": config.campaign_id,
        "campaign_configuration": common["campaign_configuration"],
        "benchmarker": common["benchmarker"],
        "engine_kit": common["engine_kit"],
        "runner_environment": common["runner_environment"],
    }
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        conflicts = [key for key, value in expected_authority.items() if current.get(key) != value]
        if conflicts:
            raise CampaignError("campaign manifest authority mismatch: " + ", ".join(conflicts))
        return path
    manifest = {
        **common,
        "manifest_type": "campaign",
        "created_at_utc": utc_now(),
        "state_transition_timestamps": [
            {"from": None, "to": "planned", "at_utc": utc_now(), "reason": "campaign-initialization"}
        ],
        "pair_bound": config.pair_count,
        "planned_pairs": planned,
        "runner_workspace_identity": path_identity(
            runner_workspace(config, runtime_root),
            "campaign-runner-workspace",
        ),
        "artifact_root_identity": path_identity(artifact_root, "durable-artifact-root"),
        "host_runtime_identity": host_identity(),
        "launch_command": launch_command,
        "ledger": "campaign_ledger.json",
        "output_file_sha256": {},
    }
    write_json(path, manifest)
    return path


def run_campaign(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    artifact_root: Path,
    launch_argv: list[str],
    *,
    max_new_pairs: int | None = None,
    executor_factory: Callable[[CampaignConfig, EngineKitSession], PairExecutor] = PairExecutor,
) -> dict[str, Any]:
    report = preflight(
        config,
        repository,
        runtime_root,
        artifact_root,
        require_clean_benchmarker=True,
        load_engine_runtime=True,
    )
    root = campaign_root(artifact_root, config)
    root.mkdir(parents=True, exist_ok=True)
    common = common_manifest(
        config,
        report["benchmarker"],
        report["engine_kit"],
        report["engine_runtime"],
        report["runner_environment"],
    )
    launch = sanitized_launch_command(
        launch_argv,
        (str(repository), str(runtime_root), str(artifact_root)),
    )
    common.update(
        {
            "runner_workspace_identity": path_identity(
                runner_workspace(config, runtime_root),
                "campaign-runner-workspace",
            ),
            "artifact_root_identity": path_identity(artifact_root, "durable-artifact-root"),
            "host_runtime_identity": host_identity(),
            "launch_command": launch,
        }
    )
    _initialize_campaign_manifest(root, config, common, runtime_root, artifact_root, launch)
    ledger = CampaignLedger(root / "campaign_ledger.json")
    with ledger.locked():
        ledger_data = ledger.initialize(
            config,
            report["benchmarker"]["commit"],
            report["engine_kit"]["source_commit"],
        )
        run_id = utc_now().replace(":", "").replace("-", "") + f"-pid{os.getpid()}"
        run_manifest_path = root / "runs" / f"run-{run_id}.json"
        run_manifest = {
            **common,
            "manifest_type": "run",
            "run_id": run_id,
            "state": "started",
            "state_transition_timestamps": [
                {"from": None, "to": "started", "at_utc": utc_now(), "reason": "operator-authorized-run"}
            ],
            "runner_workspace_identity": path_identity(
                runner_workspace(config, runtime_root),
                "campaign-runner-workspace",
            ),
            "artifact_root_identity": path_identity(artifact_root, "durable-artifact-root"),
            "host_runtime_identity": host_identity(),
            "launch_command": launch,
            "planned_pairs": [identity.to_dict() for identity in all_pair_identities(config)],
            "pair_actions": [],
            "output_file_sha256": {},
        }
        write_json(run_manifest_path, run_manifest)
        session = EngineKitSession(config)
        executor = executor_factory(config, session)
        new_pairs = 0
        stop_reason = "campaign-bound-reached"
        for identity in all_pair_identities(config):
            ledger_data = ledger.load()
            entry = ledger_data["pairs"][identity.pair_id]
            destination = pair_root(artifact_root, config, identity)
            if destination.exists():
                marker_hash = verify_committed_pair(
                    destination,
                    config,
                    identity,
                    report["benchmarker"]["commit"],
                    report["engine_kit"]["source_commit"],
                )
                if entry["state"] == "started":
                    ledger.transition(
                        identity.pair_id,
                        "committed",
                        reason="reconcile-published-pair-after-interruption",
                        committed_marker_sha256=marker_hash,
                    )
                elif entry["state"] != "committed" or entry["committed_marker_sha256"] != marker_hash:
                    raise CampaignError("ledger conflicts with immutable committed pair")
                run_manifest["pair_actions"].append({"pair_id": identity.pair_id, "action": "verified-skip"})
                continue
            if entry["state"] == "committed":
                raise CampaignError("ledger says committed but immutable pair directory is absent")
            stop_file = root / config.data["bounds"]["stop_requested_file"]
            if stop_file.exists():
                stop_reason = "stop-file-before-next-pair"
                break
            if max_new_pairs is not None and new_pairs >= max_new_pairs:
                stop_reason = "operator-max-new-pairs"
                break
            attempt = int(entry["attempt_count"]) + 1
            resume = entry["state"] == "started"
            entry = ledger.transition(
                identity.pair_id,
                "started",
                reason="resume-incomplete-pair-from-new-workspace" if resume else "start-pair-attempt",
                attempt=attempt,
            )
            workspace = pair_attempt_workspace(
                config,
                runtime_root,
                identity.pair_id,
                attempt,
            )
            workspace.mkdir(parents=True, exist_ok=False)
            try:
                execution_root = executor.run(identity, workspace)
                marker_hash = publish_pair(execution_root, artifact_root, config, identity, common, entry)
                ledger.transition(
                    identity.pair_id,
                    "committed",
                    reason="immutable-pair-publication",
                    committed_marker_sha256=marker_hash,
                )
            except KeyboardInterrupt:
                run_manifest["pair_actions"].append(
                    {"pair_id": identity.pair_id, "action": "interrupted-incomplete", "attempt": attempt}
                )
                stop_reason = "operator-interrupt-incomplete-pair"
                break
            except Exception as exc:
                ledger.transition(identity.pair_id, "failed", reason=f"attempt-failed:{type(exc).__name__}")
                run_manifest["pair_actions"].append(
                    {"pair_id": identity.pair_id, "action": "failed", "attempt": attempt, "error_type": type(exc).__name__}
                )
                stop_reason = "pair-failure"
                break
            new_pairs += 1
            run_manifest["pair_actions"].append(
                {"pair_id": identity.pair_id, "action": "committed", "attempt": attempt, "marker_sha256": marker_hash}
            )
            if stop_file.exists():
                stop_reason = "stop-file-after-committed-pair"
                break
        run_manifest["state"] = "complete"
        run_manifest["stop_reason"] = stop_reason
        run_manifest["state_transition_timestamps"].append(
            {"from": "started", "to": "complete", "at_utc": utc_now(), "reason": stop_reason}
        )
        run_manifest["output_file_sha256"] = {
            action["pair_id"]: action["marker_sha256"]
            for action in run_manifest["pair_actions"]
            if action["action"] == "committed"
        }
        write_json(run_manifest_path, run_manifest)
        return run_manifest


def campaign_status(config: CampaignConfig, artifact_root: Path) -> dict[str, Any]:
    root = campaign_root(artifact_root, config)
    ledger = CampaignLedger(root / "campaign_ledger.json")
    if not ledger.exists():
        return {"campaign_id": config.campaign_id, "status": "not-initialized"}
    data = ledger.load()
    if data.get("campaign_configuration_sha256") != config.content_sha256:
        raise CampaignError("ledger configuration differs from committed campaign config")
    counts = {state: 0 for state in ("planned", "started", "failed", "committed")}
    for pair in data["pairs"].values():
        counts[pair["state"]] += 1
    return {
        "campaign_id": config.campaign_id,
        "status": "initialized",
        "counts": counts,
        "updated_at_utc": data["updated_at_utc"],
    }
