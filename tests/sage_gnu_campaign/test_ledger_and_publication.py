from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.sage_gnu_campaign.campaign import CampaignError, publish_pair, verify_committed_pair
from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.identity import pair_identity
from runner.sage_gnu_campaign.ledger import CampaignLedger, LedgerError
from runner.sage_gnu_campaign.manifests import (
    checksum_entries,
    checksum_text,
    common_manifest,
    write_bytes_atomic,
    write_json,
)


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"
BENCHMARKER_COMMIT = "a" * 40
ENGINE_KIT_COMMIT = "833929ea72ccec058527f3cd1fa0b54a07ac666b"


class Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"2026-08-19T00:00:{self.value:02d}Z"


def initialized_ledger(tmp_path: Path) -> tuple[CampaignLedger, object]:
    config = load_campaign_config(CONFIG)
    ledger = CampaignLedger(tmp_path / "campaign_ledger.json", clock=Clock())
    ledger.initialize(config, BENCHMARKER_COMMIT, ENGINE_KIT_COMMIT)
    return ledger, config


def test_ledger_transitions_are_explicit_and_invalid_edges_fail(tmp_path: Path) -> None:
    ledger, config = initialized_ledger(tmp_path)
    identity = pair_identity(config, 1)
    started = ledger.transition(identity.pair_id, "started", reason="test", attempt=1)
    assert started["state"] == "started"
    failed = ledger.transition(identity.pair_id, "failed", reason="test-failure")
    assert failed["state"] == "failed"
    restarted = ledger.transition(identity.pair_id, "started", reason="retry", attempt=2)
    assert restarted["attempt_count"] == 2
    committed = ledger.transition(
        identity.pair_id,
        "committed",
        reason="published",
        committed_marker_sha256="c" * 64,
    )
    assert committed["state"] == "committed"
    with pytest.raises(LedgerError, match="invalid pair transition"):
        ledger.transition(identity.pair_id, "started", reason="regenerate", attempt=3)


def test_incomplete_pair_resumes_with_same_identity_and_new_attempt(tmp_path: Path) -> None:
    ledger, config = initialized_ledger(tmp_path)
    identity = pair_identity(config, 1)
    first = ledger.transition(identity.pair_id, "started", reason="start", attempt=1)
    resumed = ledger.transition(
        identity.pair_id,
        "started",
        reason="resume-incomplete-pair-from-new-workspace",
        attempt=2,
    )
    assert resumed["pair_id"] == first["pair_id"] == identity.pair_id
    assert resumed["base_seed"] == first["base_seed"] == identity.base_seed
    assert resumed["attempt_count"] == 2
    assert resumed["transitions"][-1]["from"] == "started"


def test_config_or_commit_mismatch_fails_closed(tmp_path: Path) -> None:
    ledger, config = initialized_ledger(tmp_path)
    with pytest.raises(LedgerError, match="benchmarker_commit"):
        ledger.initialize(config, "b" * 40, ENGINE_KIT_COMMIT)
    changed_path = tmp_path / "changed-campaign.json"
    changed_path.write_text(CONFIG.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed = load_campaign_config(changed_path)
    with pytest.raises(LedgerError, match="campaign_configuration_sha256"):
        ledger.initialize(changed, BENCHMARKER_COMMIT, ENGINE_KIT_COMMIT)


def execution_fixture(root: Path, identity) -> None:
    (root / "matches" / "A").mkdir(parents=True)
    (root / "matches" / "B").mkdir(parents=True)
    (root / "matches" / "A" / "decisions.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "matches" / "B" / "decisions.jsonl").write_text("{}\n", encoding="utf-8")
    write_json(
        root / "execution_result.json",
        {
            "status": "complete",
            "pair_identity": identity.to_dict(),
            "matches": [
                {"side": "A", "engine_by_physical_seat": {"O": "sage", "X": "gnu"}},
                {"side": "B", "engine_by_physical_seat": {"O": "gnu", "X": "sage"}},
            ],
        },
    )


def test_committed_pair_is_verified_and_never_regenerated(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    execution_fixture(execution, identity)
    common = {
        "schema_version": "sage-gnu-campaign-manifest-v1",
        "campaign_id": config.campaign_id,
        "campaign_configuration": {"sha256": config.content_sha256, "schema_version": config.schema_version},
        "benchmarker": {"commit": BENCHMARKER_COMMIT},
        "engine_kit": {"source_commit": ENGINE_KIT_COMMIT},
        "engine_runtime": {},
        "runner_environment": {
            "freeze_sha256": "f" * 64,
            "python": {"executable_sha256": "p" * 64},
            "engine_kit_package": {"wheel_sha256": "w" * 64},
        },
        "configured_profile": {},
    }
    ledger_pair = {
        "attempt_count": 1,
        "transitions": [{"from": "planned", "to": "started", "at_utc": "2026-08-19T00:00:00Z"}],
    }
    artifact_root = tmp_path / "artifacts"
    marker_hash = publish_pair(execution, artifact_root, config, identity, common, ledger_pair)
    committed = artifact_root / config.campaign_id / "pairs" / identity.pair_id
    assert verify_committed_pair(
        committed, config, identity, BENCHMARKER_COMMIT, ENGINE_KIT_COMMIT
    ) == marker_hash
    pair_manifest = json.loads((committed / "pair_manifest.json").read_text())
    assert pair_manifest["runner_environment"]["freeze_sha256"] == "f" * 64
    with pytest.raises(CampaignError, match="refusing to overwrite"):
        publish_pair(execution, artifact_root, config, identity, common, ledger_pair)
    with pytest.raises(CampaignError, match="benchmarker_commit"):
        verify_committed_pair(committed, config, identity, "b" * 40, ENGINE_KIT_COMMIT)


def test_checksum_manifest_is_deterministic_for_immutable_inputs(tmp_path: Path) -> None:
    root = tmp_path / "immutable"
    root.mkdir()
    (root / "z.txt").write_text("z\n", encoding="utf-8")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    first = checksum_text(checksum_entries(root))
    second = checksum_text(checksum_entries(root))
    assert first == second
    assert first.splitlines()[0].endswith(b"  a.txt")


def test_common_manifest_is_deterministic_for_immutable_inputs() -> None:
    config = load_campaign_config(CONFIG)
    benchmarker = {"repository": "repo", "branch": "branch", "commit": BENCHMARKER_COMMIT, "clean": True}
    kit = {"repository": "kit", "source_commit": ENGINE_KIT_COMMIT, "release": {}}
    runtime = {"sage": {"identity": "s"}, "gnu": {"identity": "g"}}
    environment = {"freeze_sha256": "f" * 64}
    assert common_manifest(config, benchmarker, kit, runtime, environment) == common_manifest(
        config, benchmarker, kit, runtime, environment
    )


def test_atomic_durable_writes_replace_and_fsync_file_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.manifests as manifests

    observed: list[int] = []
    real_fsync = manifests.os.fsync

    def recording_fsync(descriptor: int) -> None:
        observed.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(manifests.os, "fsync", recording_fsync)
    destination = tmp_path / "durable.json"
    write_bytes_atomic(destination, b"first\n")
    write_json(destination, {"state": "committed"})
    assert destination.read_text(encoding="utf-8") == '{\n  "state": "committed"\n}\n'
    assert len(observed) >= 4
    assert not list(tmp_path.glob(".durable.json.tmp-*"))
