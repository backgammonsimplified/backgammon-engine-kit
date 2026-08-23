from __future__ import annotations

import json
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
