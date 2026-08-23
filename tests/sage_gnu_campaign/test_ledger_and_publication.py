from __future__ import annotations

import json
import re
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
from runner.sage_gnu_campaign.match import MatchExecutionError
from tests.sage_gnu_campaign.native_fixtures import native_documents, write_execution_fixture


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
    write_execution_fixture(root, identity)


def publication_common() -> dict[str, object]:
    return {
        "benchmarker": {"commit": BENCHMARKER_COMMIT},
        "engine_kit": {"source_commit": ENGINE_KIT_COMMIT},
    }


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
    artifact_root.mkdir()
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


def test_publication_rejects_invalid_native_gnu_output(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    execution_fixture(execution, identity)
    (execution / "matches/A/native/match.sgf").write_text("", encoding="utf-8")
    common = {
        "benchmarker": {"commit": BENCHMARKER_COMMIT},
        "engine_kit": {"source_commit": ENGINE_KIT_COMMIT},
    }
    (tmp_path / "artifacts").mkdir()
    with pytest.raises(MatchExecutionError, match="SGF"):
        publish_pair(
            execution,
            tmp_path / "artifacts",
            config,
            identity,
            common,
            {"attempt_count": 1, "transitions": []},
        )
    assert not (tmp_path / "artifacts" / config.campaign_id / "pairs" / identity.pair_id).exists()


def test_publication_accepts_complete_real_shaped_multi_game_native_evidence(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    write_execution_fixture(execution, identity, [("O", 2), ("X", 1), ("O", 6)])
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    marker = publish_pair(
        execution, artifact_root, config, identity, publication_common(),
        {"attempt_count": 1, "transitions": []},
    )
    assert len(marker) == 64


@pytest.mark.parametrize(
    "case",
    [
        "text-truncated-after-game-1",
        "text-missing-final-game",
        "text-missing-middle-game",
        "sgf-one-text-multi",
        "sgf-multi-text-one",
        "sgf-missing-middle-game",
        "sgf-ordering-gap",
        "sgf-duplicated-game",
        "sgf-text-result-mismatch",
        "sgf-text-score-progression-mismatch",
        "duplicated-game-block",
        "preamble-identity",
        "trailer-identity",
        "dice-game-count-mismatch",
        "decision-game-count-mismatch",
        "decision-result-mismatch",
        "match-manifest-result-mismatch",
        "same-result-different-checker-move",
        "same-result-different-dice-sequence",
        "same-result-different-cube-action",
        "same-result-different-terminal-action",
        "root-only-sgf-with-result",
        "result-only-text-with-result",
        "reordered-actions",
        "missing-move",
        "extra-move",
        "action-belongs-to-wrong-game",
    ],
)
def test_publication_rejects_incomplete_or_cross_format_native_game_evidence(
    tmp_path: Path, case: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    games = [("O", 2), ("X", 1), ("O", 6)]
    write_execution_fixture(execution, identity, games)
    match = execution / "matches/A"
    sgf_path = match / "native/match.sgf"
    text_path = match / "native/match.txt"
    text = text_path.read_text(encoding="utf-8")
    mapping = {"O": "sage", "X": "gnu"}
    one_sgf, one_text, _ = native_documents(mapping, [("O", 8)])

    game_2 = text.index("\n Game 2")
    game_3 = text.index("\n Game 3")
    if case == "text-truncated-after-game-1":
        text_path.write_text(text[:game_2], encoding="utf-8")
    elif case == "text-missing-final-game":
        text_path.write_text(text[:game_3], encoding="utf-8")
    elif case == "text-missing-middle-game":
        text_path.write_text(text[:game_2] + text[game_3:], encoding="utf-8")
    elif case == "sgf-one-text-multi":
        sgf_path.write_text(one_sgf, encoding="utf-8")
    elif case == "sgf-multi-text-one":
        text_path.write_text(one_text, encoding="utf-8")
    elif case == "sgf-missing-middle-game":
        trees = sgf_path.read_text(encoding="utf-8").splitlines(keepends=True)
        sgf_path.write_text(trees[0] + trees[2], encoding="utf-8")
    elif case == "sgf-ordering-gap":
        sgf_path.write_text(
            sgf_path.read_text(encoding="utf-8").replace("[game:1]", "[game:3]", 1),
            encoding="utf-8",
        )
    elif case == "sgf-duplicated-game":
        trees = sgf_path.read_text(encoding="utf-8").splitlines(keepends=True)
        sgf_path.write_text(trees[0] + trees[1] + trees[1] + trees[2], encoding="utf-8")
    elif case == "sgf-text-result-mismatch":
        sgf_path.write_text(
            sgf_path.read_text(encoding="utf-8").replace("RE[W+6]", "RE[W+5]"),
            encoding="utf-8",
        )
    elif case == "sgf-text-score-progression-mismatch":
        text_path.write_text(text.replace("sage_seat_O : 2", "sage_seat_O : 3", 1), encoding="utf-8")
    elif case == "duplicated-game-block":
        text_path.write_text(text[:game_3] + text[game_2:game_3] + text[game_3:], encoding="utf-8")
    elif case == "preamble-identity":
        text_path.write_text("sage_seat_O : 0\n" + text, encoding="utf-8")
    elif case == "trailer-identity":
        text_path.write_text(text + "\ngnu_seat_X : 1\n", encoding="utf-8")
    elif case == "dice-game-count-mismatch":
        consumption = match / "dice/seat_dice_consumption.jsonl"
        records = [json.loads(line) for line in consumption.read_text(encoding="utf-8").splitlines()]
        consumption.write_text(
            "".join(json.dumps(record) + "\n" for record in records if record["game_number"] != 3),
            encoding="utf-8",
        )
    elif case == "decision-game-count-mismatch":
        decisions = match / "decisions.jsonl"
        records = [json.loads(line) for line in decisions.read_text(encoding="utf-8").splitlines()]
        decisions.write_text(
            "".join(json.dumps(record) + "\n" for record in records if record["game_number"] != 2),
            encoding="utf-8",
        )
    elif case == "decision-result-mismatch":
        decisions = match / "decisions.jsonl"
        records = [json.loads(line) for line in decisions.read_text(encoding="utf-8").splitlines()]
        records[1]["transition_evidence"]["terminal_event"]["points"] = 2
        decisions.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
    elif case == "match-manifest-result-mismatch":
        manifest_path = match / "match_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["native_evidence"]["games"][0]["points"] = 3
        write_json(manifest_path, manifest)
    elif case == "same-result-different-checker-move":
        text_path.write_text(text.replace("31: 8/5 6/5", "31: 8/4 6/5", 1), encoding="utf-8")
    elif case == "same-result-different-dice-sequence":
        text_path.write_text(text.replace("31: 8/5 6/5", "32: 8/5 6/5", 1), encoding="utf-8")
    elif case == "same-result-different-cube-action":
        text_path.write_text(text.replace("Doubles => 2                Takes", "Doubles => 2                Drops", 1), encoding="utf-8")
    elif case == "same-result-different-terminal-action":
        text_path.write_text(text.replace("65: 6/off 5/off", "Drops", 1), encoding="utf-8")
    elif case == "root-only-sgf-with-result":
        trees = sgf_path.read_text(encoding="utf-8").splitlines(keepends=True)
        trees[0] = re.sub(r";[WB]\[[^\n]*", ")\n", trees[0], count=1)
        sgf_path.write_text("".join(trees), encoding="utf-8")
    elif case == "result-only-text-with-result":
        first_result = text.index("      Wins 2 points")
        first_actions = text.index("  1)", text.index(" Game 1"))
        text_path.write_text(text[:first_actions] + text[first_result:], encoding="utf-8")
    elif case == "reordered-actions":
        text_path.write_text(
            text.replace(
                "31: 8/5 6/5                 42: 13/9 6/4",
                "42: 13/9 6/4                31: 8/5 6/5",
                1,
            ),
            encoding="utf-8",
        )
    elif case == "missing-move":
        text_path.write_text(text.replace("31: 8/5 6/5", "31: 8/5", 1), encoding="utf-8")
    elif case == "extra-move":
        text_path.write_text(text.replace("31: 8/5 6/5", "31: 8/5 6/5 13/10", 1), encoding="utf-8")
    elif case == "action-belongs-to-wrong-game":
        text_path.write_text(
            text.replace("31: 8/5 6/5", "52: 13/8 8/6", 1), encoding="utf-8"
        )
    else:  # pragma: no cover
        raise AssertionError(case)

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    with pytest.raises(MatchExecutionError):
        publish_pair(
            execution, artifact_root, config, identity, publication_common(),
            {"attempt_count": 1, "transitions": []},
        )
    assert not (artifact_root / config.campaign_id / "pairs" / identity.pair_id).exists()


def test_publication_durably_links_real_hierarchy_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.campaign as campaign_module

    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    execution_fixture(execution, identity)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    campaign = artifact_root / config.campaign_id
    pairs = campaign / "pairs"
    staging = pairs / f".{identity.pair_id}.staging-attempt-1"
    destination = pairs / identity.pair_id
    events: list[tuple[str, Path]] = []
    real_fsync_directory = campaign_module.fsync_directory
    real_fsync_tree = campaign_module.fsync_tree
    real_replace = campaign_module.os.replace
    real_verify = campaign_module.verify_committed_pair

    def recording_directory(path: Path) -> None:
        events.append(("directory", Path(path)))
        real_fsync_directory(path)

    def recording_tree(path: Path) -> None:
        events.append(("tree", Path(path)))
        real_fsync_tree(path)

    def recording_replace(source: Path, target: Path) -> None:
        events.append(("replace", Path(target)))
        real_replace(source, target)

    def recording_verify(*args, **kwargs):
        events.append(("verify", Path(args[0])))
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(campaign_module, "fsync_directory", recording_directory)
    monkeypatch.setattr(campaign_module, "fsync_tree", recording_tree)
    monkeypatch.setattr(campaign_module.os, "replace", recording_replace)
    monkeypatch.setattr(campaign_module, "verify_committed_pair", recording_verify)
    common = {
        "benchmarker": {"commit": BENCHMARKER_COMMIT},
        "engine_kit": {"source_commit": ENGINE_KIT_COMMIT},
    }
    publish_pair(
        execution, artifact_root, config, identity, common,
        {"attempt_count": 1, "transitions": []},
    )

    assert events[:4] == [
        ("directory", campaign),
        ("directory", artifact_root),
        ("directory", pairs),
        ("directory", campaign),
    ]
    first_staging_flush = events.index(("tree", staging))
    replace = events.index(("replace", destination))
    verify = events.index(("verify", destination))
    assert ("directory", pairs) in events[first_staging_flush + 1:replace]
    assert events[replace + 1] == ("directory", pairs)
    assert verify > replace + 1


def test_publication_final_parent_fsync_failure_is_reconcilable_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.campaign as campaign_module

    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    execution_fixture(execution, identity)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    destination = artifact_root / config.campaign_id / "pairs" / identity.pair_id
    real_fsync_directory = campaign_module.fsync_directory

    def fail_after_final_rename(path: Path) -> None:
        if Path(path) == destination.parent and destination.is_dir():
            raise OSError("simulated crash-window fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(campaign_module, "fsync_directory", fail_after_final_rename)
    common = {
        "benchmarker": {"commit": BENCHMARKER_COMMIT},
        "engine_kit": {"source_commit": ENGINE_KIT_COMMIT},
    }
    with pytest.raises(OSError, match="crash-window"):
        publish_pair(
            execution, artifact_root, config, identity, common,
            {"attempt_count": 1, "transitions": []},
        )
    assert destination.is_dir()
    assert verify_committed_pair(
        destination, config, identity, BENCHMARKER_COMMIT, ENGINE_KIT_COMMIT
    )


def test_publication_requires_preestablished_artifact_root(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    execution_fixture(execution, identity)
    with pytest.raises(CampaignError, match="pre-established durable"):
        publish_pair(
            execution,
            tmp_path / "absent-artifacts",
            config,
            identity,
            {"benchmarker": {"commit": BENCHMARKER_COMMIT}, "engine_kit": {"source_commit": ENGINE_KIT_COMMIT}},
            {"attempt_count": 1, "transitions": []},
        )


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


def test_verified_failed_pair_can_reconcile_directly_to_committed(tmp_path: Path) -> None:
    ledger, config = initialized_ledger(tmp_path)
    identity = pair_identity(config, 1)
    ledger.transition(identity.pair_id, "started", reason="start", attempt=1)
    ledger.transition(identity.pair_id, "failed", reason="legacy-post-publication-failure", attempt=1)
    committed = ledger.transition(
        identity.pair_id, "committed", reason="reconcile-verified-published-pair",
        committed_marker_sha256="d" * 64,
    )
    assert committed["state"] == "committed"
    assert committed["attempt_count"] == 1
