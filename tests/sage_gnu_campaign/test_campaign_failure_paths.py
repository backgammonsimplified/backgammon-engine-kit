from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.sage_gnu_campaign import campaign as campaign_module
from runner.sage_gnu_campaign import cli as cli_module
from runner.sage_gnu_campaign.campaign import campaign_root, publish_pair, run_campaign
from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.identity import pair_identity
from runner.sage_gnu_campaign.ledger import CampaignLedger
from runner.sage_gnu_campaign.manifests import common_manifest, write_json
from tests.sage_gnu_campaign.native_fixtures import write_execution_fixture

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"
BENCH = "a" * 40
KIT = "f87c69b10efa707f52aa1e42c74808d9b3bc109f"


def report(config):
    return {
        "benchmarker": {"repository": "backgammonsimplified/backgammon-engine-kit", "branch": "benchmark/sage4-gnu3-public-v1", "commit": BENCH, "clean": True},
        "engine_kit": {"source_commit": KIT, "release_commit": config.data["engine_kit"]["release_commit"]},
        "engine_runtime": {},
        "runner_environment": {"environment_content_sha256": "e" * 64},
    }


class FailingExecutor:
    def __init__(self, *_):
        pass

    def run(self, identity, workspace):
        match = workspace / "pair-output/matches/A"
        match.mkdir(parents=True)
        request = {
            "pair_id": identity.pair_id,
            "match_side": "A",
            "game_number": 1,
            "physical_seat": "O",
            "engine": "sage",
            "decision_type": "checker",
            "gnuid": "test:test",
        }
        (match / "analysis_requests.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
        (match / "analysis_results.jsonl").write_text(json.dumps({
            **request,
            "returned_result": {
                "checker_decision": {"recommended_move_id": "missing"},
                "raw_response": f"raw result from {workspace}",
            },
        }) + "\n", encoding="utf-8")
        write_json(match / "analysis_failure.json", {
            "pair_id": identity.pair_id,
            "match_side": "A",
            "game_number": 1,
            "physical_seat": "O",
            "engine": "sage",
            "decision_type": "checker",
            "gnuid": "test:test",
            "exception_type": "RuntimeError",
            "exception_message": "malformed response",
            "returned_result": {
                "checker_decision": {"recommended_move_id": "missing"},
                "raw_source": {"inline": f"raw from {workspace}", "content_sha256": "e" * 64},
            },
        })
        raise RuntimeError("malformed response")


def test_pair_failure_persists_failed_run_and_forensics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_campaign_config(CONFIG)
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    runtime = tmp_path / "runtime"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    result = run_campaign(config, REPO, runtime, artifacts, ["runner", "run"], max_new_pairs=1, executor_factory=FailingExecutor)
    assert result["state"] == "failed"
    assert result["stop_reason"] == "pair-failure"
    action = result["pair_actions"][0]
    assert action["error_type"] == "RuntimeError"
    assert action["error_message"] == "malformed response"
    failure_path = campaign_root(artifacts, config) / action["failure"]["path"]
    failure = json.loads(failure_path.read_text())
    assert failure["attempt"] == 1
    assert failure["analysis_failure_records"][0]["record"]["decision_type"] == "checker"
    durable_record = failure["analysis_failure_records"][0]["record"]
    assert durable_record["returned_result"]["checker_decision"] == {"recommended_move_id": "missing"}
    assert durable_record["returned_result"]["raw_source"]["inline"].startswith("raw from <PRIVATE_ROOT_2>")
    assert {journal["kind"] for journal in failure["analysis_journals"]} == {
        "analysis_requests", "analysis_results",
    }
    result_journal = next(
        journal for journal in failure["analysis_journals"] if journal["kind"] == "analysis_results"
    )
    result_record = json.loads((failure_path.parent / result_journal["path"]).read_text())
    assert result_record["returned_result"]["raw_response"].startswith("raw result from <PRIVATE_ROOT_2>")
    ledger = CampaignLedger(campaign_root(artifacts, config) / "campaign_ledger.json").load()
    entry = ledger["pairs"][pair_identity(config, 1).pair_id]
    assert entry["state"] == "failed"
    assert entry["transitions"][-1]["attempt"] == 1


def test_failure_bundle_hierarchy_is_durably_linked_from_campaign_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    root = tmp_path / config.campaign_id
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    failures = root / "failures"
    pair = failures / identity.pair_id
    attempt = pair / "attempt-1"
    events: list[tuple[str, Path]] = []
    real_fsync_directory = campaign_module.fsync_directory
    real_fsync_tree = campaign_module.fsync_tree

    def recording_directory(path: Path) -> None:
        events.append(("directory", Path(path)))
        real_fsync_directory(path)

    def recording_tree(path: Path) -> None:
        events.append(("tree", Path(path)))
        real_fsync_tree(path)

    monkeypatch.setattr(campaign_module, "fsync_directory", recording_directory)
    monkeypatch.setattr(campaign_module, "fsync_tree", recording_tree)
    try:
        raise RuntimeError("primary match failure")
    except RuntimeError as exc:
        persisted = campaign_module._persist_attempt_failure(
            root, identity, 1, workspace, exc, (tmp_path,)
        )
    assert persisted["path"].endswith("/attempt-1/failure.json")
    assert events == [
        ("directory", failures),
        ("directory", root),
        ("directory", pair),
        ("directory", failures),
        ("directory", attempt),
        ("directory", pair),
        ("tree", attempt),
    ]


@pytest.mark.parametrize(
    "stage",
    ["failures-parent-link", "pair-child-flush", "attempt-child-flush"],
)
def test_failure_hierarchy_crash_windows_fail_before_reporting_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    root = tmp_path / config.campaign_id
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    failures = root / "failures"
    pair = failures / identity.pair_id
    attempt = pair / "attempt-1"
    real_fsync_directory = campaign_module.fsync_directory

    def fail_at_window(path: Path) -> None:
        target = Path(path)
        should_fail = (
            stage == "failures-parent-link" and target == root and failures.exists()
            or stage == "pair-child-flush" and target == pair
            or stage == "attempt-child-flush" and target == attempt
        )
        if should_fail:
            raise OSError(f"simulated {stage}")
        real_fsync_directory(path)

    monkeypatch.setattr(campaign_module, "fsync_directory", fail_at_window)
    try:
        raise RuntimeError("primary match failure")
    except RuntimeError as exc:
        with pytest.raises(OSError, match=stage):
            campaign_module._persist_attempt_failure(
                root, identity, 1, workspace, exc, (tmp_path,)
            )
    assert failures.is_dir()
    if stage != "failures-parent-link":
        assert pair.is_dir()
    if stage == "attempt-child-flush":
        assert attempt.is_dir()


def test_failure_bundle_file_is_fsynced_before_attempt_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runner.sage_gnu_campaign.manifests as manifests_module

    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    root = tmp_path / config.campaign_id
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[tuple[str, Path]] = []
    real_fsync = manifests_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        events.append((kind, Path(os.readlink(f"/proc/self/fd/{descriptor}"))))
        real_fsync(descriptor)

    monkeypatch.setattr(manifests_module.os, "fsync", recording_fsync)
    try:
        raise RuntimeError("primary match failure")
    except RuntimeError as exc:
        campaign_module._persist_attempt_failure(
            root, identity, 1, workspace, exc, (tmp_path,)
        )
    attempt = root / "failures" / identity.pair_id / "attempt-1"
    file_index = next(
        index for index, event in enumerate(events)
        if event[0] == "file" and ".failure.json.tmp-" in event[1].name
    )
    directory_index = next(
        index for index, event in enumerate(events[file_index + 1:], file_index + 1)
        if event == ("directory", attempt)
    )
    assert file_index < directory_index


def test_failure_after_bundle_file_fsync_before_directory_fsync_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runner.sage_gnu_campaign.manifests as manifests_module

    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    root = tmp_path / config.campaign_id
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attempt = root / "failures" / identity.pair_id / "attempt-1"
    real_fsync_directory = manifests_module.fsync_directory

    def fail_attempt_directory(path: Path) -> None:
        if Path(path) == attempt and (attempt / "failure.json").exists():
            raise OSError("simulated bundle directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(manifests_module, "fsync_directory", fail_attempt_directory)
    try:
        raise RuntimeError("primary match failure")
    except RuntimeError as exc:
        with pytest.raises(OSError, match="bundle directory fsync"):
            campaign_module._persist_attempt_failure(
                root, identity, 1, workspace, exc, (tmp_path,)
            )
    assert (attempt / "failure.json").is_file()


def test_forensic_persistence_failure_does_not_mask_primary_match_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_campaign_config(CONFIG)
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    primary = RuntimeError("original engine failure")

    class PrimaryFailingExecutor:
        def __init__(self, *_: object) -> None:
            pass

        def run(self, identity, workspace):
            raise primary

    def fail_persistence(*args, **kwargs):
        raise OSError("secondary forensic storage failure")

    monkeypatch.setattr(campaign_module, "_persist_attempt_failure", fail_persistence)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    result = run_campaign(
        config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
        max_new_pairs=1, executor_factory=PrimaryFailingExecutor,
    )
    action = result["pair_actions"][0]
    assert result["state"] == "failed"
    assert action["error_type"] == "RuntimeError"
    assert action["error_message"] == "original engine failure"
    assert action["failure"] == {
        "status": "persistence-failed",
        "error_type": "OSError",
        "error_message": "secondary forensic storage failure",
    }
    assert any("secondary forensic storage failure" in note for note in primary.__notes__)


def test_cli_returns_nonzero_for_failed_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(cli_module, "run_campaign", lambda *a, **k: {"state": "failed", "stop_reason": "pair-failure"})
    rc = cli_module.main([
        "--config", str(CONFIG), "run",
        "--runtime-root", str(tmp_path / "runtime"),
        "--artifact-root", str(tmp_path / "artifacts"),
        "--authorize-real-match", "--max-new-pairs", "1",
    ])
    assert rc == 1
    assert '"state": "failed"' in capsys.readouterr().out


def test_verified_published_pair_reconciles_from_failed_without_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    root = campaign_root(artifacts, config)
    ledger = CampaignLedger(root / "campaign_ledger.json")
    ledger.initialize(config, BENCH, KIT)
    ledger.transition(identity.pair_id, "started", reason="start", attempt=1)
    ledger.transition(identity.pair_id, "failed", reason="legacy", attempt=1)
    execution = tmp_path / "execution"
    write_execution_fixture(execution, identity)
    common = common_manifest(config, report(config)["benchmarker"], report(config)["engine_kit"], {}, report(config)["runner_environment"])
    publish_pair(execution, artifacts, config, identity, common, {"attempt_count": 1, "transitions": []})
    (root / config.data["bounds"]["stop_requested_file"]).write_text("stop\n", encoding="utf-8")
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    class Never:
        def __init__(self, *_): pass
        def run(self, *_): raise AssertionError("executor must not run")
    result = run_campaign(config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"], max_new_pairs=1, executor_factory=Never)
    assert result["pair_actions"][0]["action"] == "verified-skip"
    assert CampaignLedger(root / "campaign_ledger.json").load()["pairs"][identity.pair_id]["state"] == "committed"
