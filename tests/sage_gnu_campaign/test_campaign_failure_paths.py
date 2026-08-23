from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.sage_gnu_campaign import campaign as campaign_module
from runner.sage_gnu_campaign import cli as cli_module
from runner.sage_gnu_campaign import ledger as ledger_module
from runner.sage_gnu_campaign import manifests as manifests_module
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


class SuccessfulFixtureExecutor:
    def __init__(self, *_):
        pass

    def run(self, identity, workspace):
        output = workspace / "pair-output"
        write_execution_fixture(output, identity)
        return output


class ExecutorMustNotRun:
    def __init__(self, *_):
        pass

    def run(self, identity, workspace):
        raise AssertionError(f"committed pair was unexpectedly re-executed: {identity.pair_id}")


@pytest.mark.parametrize("stage", ["before-synchronization", "during-synchronization"])
def test_final_manifest_write_interrupt_never_removes_live_run_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    primary = KeyboardInterrupt(f"interrupt-{stage}")
    action = {
        "pair_id": "pair-01",
        "action": "committed",
        "marker_sha256": "a" * 64,
    }
    manifest = {
        "campaign_id": "campaign-test",
        "run_id": "run-test",
        "state": "started",
        "state_transition_timestamps": [
            {"from": None, "to": "started", "at_utc": "before", "reason": "test"}
        ],
        "planned_pairs": [{"pair_id": "pair-01", "base_seed": "seed-01"}],
        "pair_actions": [action],
        "output_file_sha256": {},
    }

    def interrupt_synchronization(live, finalized):
        assert json.loads((tmp_path / "run.json").read_text()) == finalized
        assert live["run_id"] == "run-test"
        assert live["campaign_id"] == "campaign-test"
        assert live["pair_actions"] == [action]
        assert live["planned_pairs"] == [
            {"pair_id": "pair-01", "base_seed": "seed-01"}
        ]
        if stage == "during-synchronization":
            live["state"] = finalized["state"]
        raise primary

    monkeypatch.setattr(
        campaign_module, "_synchronize_run_manifest", interrupt_synchronization
    )
    with pytest.raises(KeyboardInterrupt) as caught:
        campaign_module._finalize_run_manifest(
            tmp_path / "run.json", manifest, "complete", "test-complete"
        )
    assert caught.value is primary
    assert manifest["run_id"] == "run-test"
    assert manifest["campaign_id"] == "campaign-test"
    assert manifest["pair_actions"] == [action]
    assert manifest["planned_pairs"][0]["base_seed"] == "seed-01"

    evidence = campaign_module._persist_run_interruption(
        tmp_path, manifest, primary, (), {"status": "pass"}
    )
    assert json.loads((tmp_path / evidence["path"]).read_text())[
        "exception_type"
    ] == "KeyboardInterrupt"


def test_normal_run_manifest_finalization_preserves_identity_and_matches_disk(
    tmp_path: Path,
) -> None:
    action = {
        "pair_id": "pair-01",
        "action": "committed",
        "marker_sha256": "b" * 64,
    }
    manifest = {
        "campaign_id": "campaign-test",
        "run_id": "run-test",
        "state": "started",
        "stop_reason": "stale-stop-reason",
        "state_transition_timestamps": [
            {"from": None, "to": "started", "at_utc": "before", "reason": "test"}
        ],
        "planned_pairs": [{"pair_id": "pair-01", "base_seed": "seed-01"}],
        "pair_actions": [action],
        "output_file_sha256": {"stale-pair": "stale-marker"},
    }
    original_identity = id(manifest)

    result = campaign_module._finalize_run_manifest(
        tmp_path / "run.json", manifest, "complete", "test-complete"
    )

    assert result is manifest
    assert id(result) == original_identity
    assert result == json.loads((tmp_path / "run.json").read_text())
    assert result["state"] == "complete"
    assert result["stop_reason"] == "test-complete"
    assert result["output_file_sha256"] == {"pair-01": "b" * 64}
    assert "stale-pair" not in result["output_file_sha256"]


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


@pytest.mark.parametrize(
    ("stage", "published"),
    [
        ("before-final-rename", False),
        ("after-final-rename", True),
        ("after-durable-publication", True),
        ("during-ledger-commit", True),
        ("before-run-finalization", True),
        ("after-durable-final-manifest-write", True),
        ("during-in-memory-manifest-synchronization", True),
    ],
)
def test_keyboard_interrupt_is_preserved_across_publication_and_ledger_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, published: bool,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    destination = campaign_root(artifacts, config) / "pairs" / identity.pair_id
    primary = KeyboardInterrupt(f"interrupt-{stage}")
    fired = False

    if stage == "before-final-rename":
        real_replace = campaign_module.os.replace

        def interrupt_before_replace(source, target):
            nonlocal fired
            if Path(target) == destination and not fired:
                fired = True
                raise primary
            return real_replace(source, target)

        monkeypatch.setattr(campaign_module.os, "replace", interrupt_before_replace)
    elif stage == "after-final-rename":
        real_fsync_directory = campaign_module.fsync_directory

        def interrupt_after_replace(path: Path) -> None:
            nonlocal fired
            if Path(path) == destination.parent and destination.exists() and not fired:
                fired = True
                raise primary
            real_fsync_directory(path)

        monkeypatch.setattr(campaign_module, "fsync_directory", interrupt_after_replace)
    elif stage == "after-durable-publication":
        real_publish = campaign_module.publish_pair

        def interrupt_after_publish(*args, **kwargs):
            nonlocal fired
            marker = real_publish(*args, **kwargs)
            fired = True
            raise primary

        monkeypatch.setattr(campaign_module, "publish_pair", interrupt_after_publish)
    elif stage == "during-ledger-commit":
        real_transition = CampaignLedger.transition

        def interrupt_ledger(self, pair_id, target, **kwargs):
            nonlocal fired
            if target == "committed" and not fired:
                fired = True
                raise primary
            return real_transition(self, pair_id, target, **kwargs)

        monkeypatch.setattr(CampaignLedger, "transition", interrupt_ledger)
    elif stage in {
        "after-durable-final-manifest-write",
        "during-in-memory-manifest-synchronization",
    }:
        real_synchronize = campaign_module._synchronize_run_manifest

        def interrupt_synchronization(manifest, finalized):
            nonlocal fired
            if finalized["stop_reason"] == "operator-max-new-pairs" and not fired:
                assert manifest["run_id"]
                assert manifest["campaign_id"] == config.campaign_id
                assert manifest["pair_actions"][-1]["action"] == "committed"
                assert manifest["planned_pairs"][0]["pair_id"] == identity.pair_id
                if stage == "during-in-memory-manifest-synchronization":
                    manifest["state"] = finalized["state"]
                fired = True
                raise primary
            real_synchronize(manifest, finalized)

        monkeypatch.setattr(
            campaign_module, "_synchronize_run_manifest", interrupt_synchronization
        )
    else:
        real_finalize = campaign_module._finalize_run_manifest

        def interrupt_finalization(*args, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                raise primary
            return real_finalize(*args, **kwargs)

        monkeypatch.setattr(campaign_module, "_finalize_run_manifest", interrupt_finalization)

    with pytest.raises(KeyboardInterrupt) as caught:
        run_campaign(
            config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
            max_new_pairs=1, executor_factory=SuccessfulFixtureExecutor,
        )
    assert caught.value is primary
    assert fired
    ledger = CampaignLedger(
        campaign_root(artifacts, config) / "campaign_ledger.json"
    ).load()["pairs"][identity.pair_id]
    assert ledger["state"] == ("committed" if published else "started")
    assert destination.exists() is published
    if published:
        marker = campaign_module.verify_committed_pair(
            destination, config, identity, BENCH, KIT
        )
        assert ledger["committed_marker_sha256"] == marker
    run_path = next((campaign_root(artifacts, config) / "runs").glob("run-*.json"))
    run = json.loads(run_path.read_text())
    assert run["state"] == "interrupted"
    assert run["pair_actions"][-1]["action"] == (
        "committed-interrupted" if published else "interrupted-incomplete"
    )
    failure_path = campaign_root(artifacts, config) / run["pair_actions"][-1]["failure"]["path"]
    assert failure_path.is_file()
    assert json.loads(failure_path.read_text())["exception_type"] == "KeyboardInterrupt"


def test_interrupt_persistence_failure_never_replaces_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_campaign_config(CONFIG)
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    primary = KeyboardInterrupt("primary operator interrupt")

    def interrupt_publication(*args, **kwargs):
        raise primary

    def fail_persistence(*args, **kwargs):
        raise OSError("secondary interruption persistence failure")

    monkeypatch.setattr(campaign_module, "publish_pair", interrupt_publication)
    monkeypatch.setattr(campaign_module, "_persist_attempt_failure", fail_persistence)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with pytest.raises(KeyboardInterrupt) as caught:
        run_campaign(
            config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
            max_new_pairs=1, executor_factory=SuccessfulFixtureExecutor,
        )
    assert caught.value is primary
    assert any("secondary interruption persistence failure" in note for note in primary.__notes__)
    run_path = next((campaign_root(artifacts, config) / "runs").glob("run-*.json"))
    run = json.loads(run_path.read_text())
    assert run["state"] == "interrupted"
    assert run["pair_actions"][-1]["failure"]["status"] == "persistence-failed"


@pytest.mark.parametrize("retry_fails", [False, True], ids=["retry-succeeds", "retry-fails"])
def test_interrupt_recovery_completes_pair_parent_before_ledger_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retry_fails: bool,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    root = campaign_root(artifacts, config)
    pairs = root / "pairs"
    destination = pairs / identity.pair_id
    primary = KeyboardInterrupt("interrupt after final pair rename")
    events: list[str] = []
    pair_parent_calls = 0
    real_fsync_directory = campaign_module.fsync_directory
    real_transition = CampaignLedger.transition

    def pair_parent_barrier(path: Path) -> None:
        nonlocal pair_parent_calls
        target = Path(path)
        if target == pairs and destination.exists():
            pair_parent_calls += 1
            if pair_parent_calls == 1:
                events.append("pair-parent-original-interrupt")
                raise primary
            events.append("pair-parent-recovery")
            if retry_fails:
                raise OSError("secondary pair-parent durability failure")
        elif target == root and pair_parent_calls:
            events.append("ledger-parent-recovery")
        real_fsync_directory(path)

    def record_transition(self, pair_id, target, **kwargs):
        if target == "committed":
            events.append("ledger-commit")
        return real_transition(self, pair_id, target, **kwargs)

    monkeypatch.setattr(campaign_module, "fsync_directory", pair_parent_barrier)
    monkeypatch.setattr(CampaignLedger, "transition", record_transition)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_campaign(
            config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
            max_new_pairs=1, executor_factory=SuccessfulFixtureExecutor,
        )
    assert caught.value is primary
    assert destination.is_dir()
    run_path = next((root / "runs").glob("run-*.json"))
    action = json.loads(run_path.read_text())["pair_actions"][-1]
    if retry_fails:
        assert "ledger-commit" not in events
        assert action["action"] == "published-ledger-pending-interrupted"
        assert action["pair_directory_durable"] is False
        assert action["ledger_transition_durable"] is False
        assert any("secondary pair-parent durability failure" in note for note in primary.__notes__)
    else:
        assert events.index("pair-parent-recovery") < events.index("ledger-commit")
        assert events.index("ledger-commit") < events.index("ledger-parent-recovery")
        assert action["action"] == "committed-interrupted"
        assert action["pair_directory_durable"] is True
        assert action["ledger_transition_durable"] is True


@pytest.mark.parametrize("retry_fails", [False, True], ids=["retry-succeeds", "retry-fails"])
def test_interrupt_recovery_retries_visible_ledger_parent_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retry_fails: bool,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    root = campaign_root(artifacts, config)
    pairs = root / "pairs"
    destination = pairs / identity.pair_id
    ledger_path = root / "campaign_ledger.json"
    primary = KeyboardInterrupt("interrupt after visible ledger replacement")
    events: list[str] = []
    original_interrupted = False
    recovery_pair_flushed = False
    real_ledger_fsync = ledger_module.os.fsync
    real_campaign_fsync = campaign_module.fsync_directory

    def interrupt_committed_ledger_parent(descriptor: int) -> None:
        nonlocal original_interrupted
        descriptor_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if (
            not original_interrupted
            and stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and descriptor_path == root
            and ledger_path.is_file()
        ):
            data = json.loads(ledger_path.read_text())
            if data["pairs"][identity.pair_id]["state"] == "committed":
                original_interrupted = True
                events.append("ledger-parent-original-interrupt")
                raise primary
        real_ledger_fsync(descriptor)

    def recovery_barriers(path: Path) -> None:
        nonlocal recovery_pair_flushed
        target = Path(path)
        if original_interrupted and target == pairs:
            recovery_pair_flushed = True
            events.append("pair-parent-recovery")
        elif original_interrupted and target == root and recovery_pair_flushed:
            events.append("ledger-parent-recovery")
            if retry_fails and events.count("ledger-parent-recovery") == 1:
                raise OSError("secondary ledger-parent durability failure")
        real_campaign_fsync(path)

    monkeypatch.setattr(ledger_module.os, "fsync", interrupt_committed_ledger_parent)
    monkeypatch.setattr(campaign_module, "fsync_directory", recovery_barriers)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_campaign(
            config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
            max_new_pairs=1, executor_factory=SuccessfulFixtureExecutor,
        )
    assert caught.value is primary
    assert destination.is_dir()
    ledger_entry = CampaignLedger(ledger_path).load()["pairs"][identity.pair_id]
    assert ledger_entry["state"] == "committed"
    assert events.index("pair-parent-recovery") < events.index("ledger-parent-recovery")
    run_path = next((root / "runs").glob("run-*.json"))
    action = json.loads(run_path.read_text())["pair_actions"][-1]
    if retry_fails:
        assert action["action"] == "published-ledger-pending-interrupted"
        assert action["ledger_transition_durable"] is False
        assert any("secondary ledger-parent durability failure" in note for note in primary.__notes__)
    else:
        assert action["action"] == "committed-interrupted"
        assert action["ledger_transition_durable"] is True


@pytest.mark.parametrize(
    "stage",
    [
        "immediately-before",
        "during-temp-write",
        "after-replace",
        "after-durable-final-manifest-write",
        "during-in-memory-manifest-synchronization",
        "no-active-pair",
    ],
)
def test_campaign_bound_finalization_interrupt_is_run_level_and_restartable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identities = [pair_identity(config, index) for index in range(1, 11)]
    monkeypatch.setattr(campaign_module, "preflight", lambda *a, **k: report(config))
    monkeypatch.setattr(campaign_module, "EngineKitSession", lambda _: object())
    clock_tick = 0

    def clock() -> str:
        nonlocal clock_tick
        clock_tick += 1
        return f"2026-08-23T12:{clock_tick // 60:02d}:{clock_tick % 60:02d}Z"

    monkeypatch.setattr(campaign_module, "utc_now", clock)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    root = campaign_root(artifacts, config)
    ledger_path = root / "campaign_ledger.json"
    primary = KeyboardInterrupt(f"campaign-bound-{stage}")
    fired = False

    class FinalizationOnlyExecutor:
        def __init__(self, *_):
            pass

        def run(self, identity, workspace):
            output = workspace / "pair-output"
            output.mkdir()
            return output

    def lightweight_publish(execution, artifact_root, config, identity, common, entry):
        del execution, common, entry
        destination = campaign_module.pair_root(artifact_root, config, identity)
        destination.mkdir(parents=True)
        marker = identity.base_seed.removeprefix("sha256:")
        write_json(destination / "_COMMITTED.json", {"marker_sha256": marker})
        return marker

    def lightweight_verify(root, config, identity, benchmarker_commit, engine_kit_commit):
        del config, benchmarker_commit, engine_kit_commit
        marker = json.loads((Path(root) / "_COMMITTED.json").read_text())["marker_sha256"]
        assert marker == identity.base_seed.removeprefix("sha256:")
        return marker

    monkeypatch.setattr(campaign_module, "publish_pair", lightweight_publish)
    monkeypatch.setattr(campaign_module, "verify_committed_pair", lightweight_verify)

    def all_committed() -> bool:
        if not ledger_path.is_file():
            return False
        data = json.loads(ledger_path.read_text())
        return all(
            data["pairs"][identity.pair_id]["state"] == "committed"
            for identity in identities
        )

    if stage in {"immediately-before", "no-active-pair"}:
        real_finalize = campaign_module._finalize_run_manifest

        def interrupt_before_bound(path, manifest, state, stop_reason):
            nonlocal fired
            if stop_reason == "campaign-bound-reached" and not fired:
                assert all_committed()
                assert len(manifest["pair_actions"]) == 10
                assert all(action["action"] == "committed" for action in manifest["pair_actions"])
                assert "active_pair" not in manifest
                fired = True
                raise primary
            return real_finalize(path, manifest, state, stop_reason)

        monkeypatch.setattr(campaign_module, "_finalize_run_manifest", interrupt_before_bound)
    elif stage == "during-temp-write":
        real_fsync = manifests_module.os.fsync

        def interrupt_run_temp_fsync(descriptor: int) -> None:
            nonlocal fired
            descriptor_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            if (
                not fired
                and stat.S_ISREG(os.fstat(descriptor).st_mode)
                and descriptor_path.name.startswith(".run-")
                and all_committed()
            ):
                fired = True
                raise primary
            real_fsync(descriptor)

        monkeypatch.setattr(manifests_module.os, "fsync", interrupt_run_temp_fsync)
    elif stage == "after-replace":
        real_fsync_directory = manifests_module.fsync_directory

        def interrupt_after_run_replace(path: Path) -> None:
            nonlocal fired
            target = Path(path)
            visible_runs = list((root / "runs").glob("run-*.json")) if (root / "runs").is_dir() else []
            visible_complete = any(
                json.loads(run.read_text()).get("stop_reason") == "campaign-bound-reached"
                for run in visible_runs
            )
            if not fired and target == root / "runs" and all_committed() and visible_complete:
                fired = True
                raise primary
            real_fsync_directory(path)

        monkeypatch.setattr(manifests_module, "fsync_directory", interrupt_after_run_replace)
    else:
        real_synchronize = campaign_module._synchronize_run_manifest

        def interrupt_synchronization(manifest, finalized):
            nonlocal fired
            if finalized["stop_reason"] == "campaign-bound-reached" and not fired:
                assert all_committed()
                assert manifest["run_id"]
                assert manifest["campaign_id"] == config.campaign_id
                assert len(manifest["pair_actions"]) == 10
                assert len(manifest["planned_pairs"]) == 10
                if stage == "during-in-memory-manifest-synchronization":
                    manifest["state"] = finalized["state"]
                fired = True
                raise primary
            real_synchronize(manifest, finalized)

        monkeypatch.setattr(
            campaign_module, "_synchronize_run_manifest", interrupt_synchronization
        )

    try:
        unexpected = run_campaign(
            config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
            executor_factory=FinalizationOnlyExecutor,
        )
    except KeyboardInterrupt as caught:
        assert caught is primary
    else:
        pytest.fail(f"campaign-bound interrupt did not fire: {unexpected!r}")
    assert fired
    ledger = CampaignLedger(ledger_path).load()
    assert all(ledger["pairs"][identity.pair_id]["state"] == "committed" for identity in identities)
    assert all((root / "pairs" / identity.pair_id).is_dir() for identity in identities)
    interrupted_runs = [
        json.loads(path.read_text()) for path in (root / "runs").glob("run-*.json")
        if json.loads(path.read_text()).get("state") == "interrupted"
    ]
    assert len(interrupted_runs) == 1
    interrupted = interrupted_runs[0]
    assert len(interrupted["pair_actions"]) == 10
    assert not any("failed" in action["action"] for action in interrupted["pair_actions"])
    interruption = interrupted["run_interruption"]
    assert interruption["active_pair"] is None
    evidence_path = root / interruption["path"]
    evidence = json.loads(evidence_path.read_text())
    assert evidence["active_pair"] is None
    assert evidence["committed_pair_count"] == 10
    assert evidence["last_committed_pair_id"] == identities[-1].pair_id
    assert evidence["authoritative_state_verification"]["status"] == "pass"
    assert evidence["exception_type"] == "KeyboardInterrupt"

    restarted = run_campaign(
        config, REPO, tmp_path / "runtime", artifacts, ["runner", "run"],
        executor_factory=ExecutorMustNotRun,
    )
    assert restarted["state"] == "complete"
    assert restarted["stop_reason"] == "campaign-bound-reached"
    assert len(restarted["pair_actions"]) == 10
    assert all(action["action"] == "verified-skip" for action in restarted["pair_actions"])
    assert evidence_path.is_file()


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
