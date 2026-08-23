from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path

import pytest

from runner.sage_gnu_campaign.campaign import CampaignError, publish_pair, verify_committed_pair
from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.dice import dice_record, namespace_seed, stream_id
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
    "terminal_kind,games",
    [
        ("drop", [("O", 1), ("O", 6)]),
        ("resignation", [("O", 2), ("O", 6)]),
    ],
)
def test_publication_accepts_exact_drop_and_resignation_semantics(
    tmp_path: Path, terminal_kind: str, games: list[tuple[str, int]],
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    write_execution_fixture(
        execution, identity, games, [terminal_kind, "ordinary_game_over"]
    )
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
        "resignation-encoded-as-normal",
        "normal-encoded-as-resignation",
        "pass-encoded-as-resignation",
        "wrong-resignation-level",
        "wrong-command-type",
        "wrong-acting-seat",
        "wrong-acting-engine",
        "wrong-pre-command-score",
        "wrong-post-command-score",
        "missing-subsequent-opening",
        "subsequent-opening-wrong-game",
        "reordered-decision-records",
        "reordered-dice-records",
        "duplicated-dice-record",
        "missing-dice-record",
        "wrong-roll-index",
        "correct-dice-wrong-physical-seat",
        "correct-dice-wrong-engine",
        "wrong-stream-identity",
        "wrong-pair-seed",
        "wrong-match-side",
    ],
)
def test_publish_pair_rejects_terminal_and_ordered_journal_corruption(
    tmp_path: Path, case: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    terminal_kinds = None
    games = [("O", 2), ("X", 1), ("O", 6)]
    if case in {"resignation-encoded-as-normal", "wrong-resignation-level"}:
        games = [("O", 2), ("O", 6)]
        terminal_kinds = ["resignation", "ordinary_game_over"]
    elif case == "normal-encoded-as-resignation":
        games = [("O", 8)]
    elif case == "pass-encoded-as-resignation":
        games = [("O", 1), ("O", 6)]
        terminal_kinds = ["drop", "ordinary_game_over"]
    write_execution_fixture(execution, identity, games, terminal_kinds)
    match = execution / "matches/A"
    decision_path = match / "decisions.jsonl"
    decisions = [json.loads(line) for line in decision_path.read_text(encoding="utf-8").splitlines()]
    terminal_index = next(
        index for index, record in enumerate(decisions)
        if record["transition_evidence"]["terminal_event"] is not None
    )
    terminal = decisions[terminal_index]["transition_evidence"]

    if case == "resignation-encoded-as-normal":
        terminal["terminal_event"]["kind"] = "ordinary_game_over"
        terminal["terminal_event"].pop("resignation_level")
    elif case == "normal-encoded-as-resignation":
        terminal["terminal_event"]["kind"] = "resignation"
        terminal["terminal_event"]["resignation_level"] = terminal["terminal_event"]["result_level"]
    elif case == "pass-encoded-as-resignation":
        terminal["terminal_event"]["kind"] = "resignation"
        terminal["terminal_event"]["resignation_level"] = 1
        terminal["terminal_event"].pop("loser_physical_seat")
        terminal["terminal_event"].pop("loser_engine")
    elif case == "wrong-resignation-level":
        terminal["terminal_event"]["resignation_level"] = 3
    elif case == "wrong-command-type":
        terminal["command_type"] = "unsupported"
    elif case == "wrong-acting-seat":
        terminal["acting_physical_seat"] = "X" if terminal["acting_physical_seat"] == "O" else "O"
    elif case == "wrong-acting-engine":
        terminal["acting_engine"] = "gnu" if terminal["acting_engine"] == "sage" else "sage"
    elif case == "wrong-pre-command-score":
        terminal["pre_command"]["score"][0] += 1
    elif case == "wrong-post-command-score":
        terminal["post_command"]["score"][0] += 1
    elif case == "missing-subsequent-opening":
        terminal["subsequent_opening_state"] = None
    elif case == "subsequent-opening-wrong-game":
        terminal["subsequent_opening_state"]["game_number"] += 1
    elif case == "reordered-decision-records":
        decisions[0], decisions[1] = decisions[1], decisions[0]

    dice_path = match / "dice/seat_dice_consumption.jsonl"
    dice_records = [json.loads(line) for line in dice_path.read_text(encoding="utf-8").splitlines()]
    if case == "reordered-dice-records":
        dice_records[0], dice_records[1] = dice_records[1], dice_records[0]
        for ordinal, record in enumerate(dice_records, 1):
            record["consumption_ordinal"] = ordinal
    elif case == "duplicated-dice-record":
        dice_records.insert(1, dict(dice_records[0]))
        for ordinal, record in enumerate(dice_records, 1):
            record["consumption_ordinal"] = ordinal
    elif case == "missing-dice-record":
        del dice_records[0]
        for ordinal, record in enumerate(dice_records, 1):
            record["consumption_ordinal"] = ordinal
    elif case == "wrong-roll-index":
        dice_records[0]["roll_index"] += 1
    elif case == "correct-dice-wrong-physical-seat":
        dice_records[0]["physical_seat"] = "X"
    elif case == "correct-dice-wrong-engine":
        dice_records[0]["engine"] = "gnu"
    elif case == "wrong-stream-identity":
        dice_records[0]["stream_id"] = "stream-" + "0" * 64
    elif case == "wrong-pair-seed":
        dice_records[0]["base_seed"] = "sha256:" + "0" * 64
    elif case == "wrong-match-side":
        dice_records[0]["match_side"] = "B"

    decision_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )
    dice_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in dice_records),
        encoding="utf-8",
    )
    dice_manifest_path = match / "dice/seat_dice_manifest.json"
    dice_manifest = json.loads(dice_manifest_path.read_text(encoding="utf-8"))
    dice_manifest["consumption"]["entries"] = len(dice_records)
    dice_manifest["consumption"]["sha256"] = hashlib.sha256(dice_path.read_bytes()).hexdigest()
    write_json(dice_manifest_path, dice_manifest)

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    with pytest.raises(MatchExecutionError):
        publish_pair(
            execution, artifact_root, config, identity, publication_common(),
            {"attempt_count": 1, "transitions": []},
        )
    assert not (artifact_root / config.campaign_id / "pairs" / identity.pair_id).exists()


@pytest.mark.parametrize(
    "case",
    [
        "disconnected-adjacent-gnuids",
        "swapped-with-rewritten-ordinals",
        "fabricated-plausible-gnuid",
        "missing-roll",
        "extra-roll",
        "roll-at-wrong-point",
        "wrong-checker-state",
        "wrong-cube-state",
        "wrong-terminal-state",
        "wrong-automatic-opening",
        "wrong-seat",
        "wrong-engine",
    ],
)
def test_publication_requires_exact_connected_decision_state_machine(
    tmp_path: Path, case: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    games = [("O", 2), ("O", 6)] if case == "wrong-automatic-opening" else None
    write_execution_fixture(execution, identity, games)
    match = execution / "matches/A"
    decision_path = match / "decisions.jsonl"
    decisions = [json.loads(line) for line in decision_path.read_text(encoding="utf-8").splitlines()]
    rolls = [index for index, record in enumerate(decisions) if record["command"] == "roll"]
    checkers = [
        index for index, record in enumerate(decisions)
        if record["transition_evidence"]["command_type"] == "checker"
    ]
    doubles = [index for index, record in enumerate(decisions) if record["command"] == "double"]
    terminal_index = next(
        index for index, record in enumerate(decisions)
        if record["transition_evidence"]["terminal_event"] is not None
    )

    if case == "disconnected-adjacent-gnuids":
        replacement = decisions[-1]["transition_evidence"]["post_command"]["gnuid"]
        decisions[1]["gnuid"] = replacement
        decisions[1]["transition_evidence"]["pre_command"]["gnuid"] = replacement
    elif case == "swapped-with-rewritten-ordinals":
        decisions[1], decisions[2] = decisions[2], decisions[1]
    elif case == "fabricated-plausible-gnuid":
        decisions[0]["transition_evidence"]["post_command"]["gnuid"] = decisions[4]["gnuid"]
    elif case == "missing-roll":
        del decisions[rolls[0]]
    elif case == "extra-roll":
        decisions.insert(rolls[0] + 1, dict(decisions[rolls[0]]))
    elif case == "roll-at-wrong-point":
        index = rolls[0]
        decisions[index], decisions[index + 1] = decisions[index + 1], decisions[index]
    elif case == "wrong-checker-state":
        decisions[checkers[1]]["transition_evidence"]["post_command"]["gnuid"] = decisions[-1]["gnuid"]
    elif case == "wrong-cube-state":
        decisions[doubles[0]]["transition_evidence"]["post_command"]["gnuid"] = decisions[doubles[0]]["gnuid"]
    elif case == "wrong-terminal-state":
        decisions[terminal_index]["transition_evidence"]["post_command"]["gnuid"] = decisions[terminal_index]["gnuid"]
    elif case == "wrong-automatic-opening":
        transition = decisions[terminal_index]["transition_evidence"]
        wrong = decisions[terminal_index]["gnuid"]
        transition["post_command"]["gnuid"] = wrong
        transition["subsequent_opening_state"]["gnuid"] = wrong
    elif case == "wrong-seat":
        decisions[checkers[0]]["physical_seat"] = (
            "X" if decisions[checkers[0]]["physical_seat"] == "O" else "O"
        )
    elif case == "wrong-engine":
        decisions[checkers[0]]["engine"] = (
            "gnu" if decisions[checkers[0]]["engine"] == "sage" else "sage"
        )
    for ordinal, record in enumerate(decisions, 1):
        record["record_ordinal"] = ordinal
        record["decision_ordinal"] = ordinal
    decision_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    with pytest.raises(MatchExecutionError):
        publish_pair(
            execution, artifact_root, config, identity, publication_common(),
            {"attempt_count": 1, "transitions": []},
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing-request", "missing-result", "duplicate", "reordered",
        "wrong-decision-association", "wrong-engine", "wrong-seat", "wrong-gnuid",
        "wrong-type", "malformed-result", "wrong-configured-depth",
        "invalid-actual-depth", "cross-side", "cross-game", "cross-pair",
        "missing-raw-source", "malformed-candidates", "wrong-manifest-path",
        "manifest-file-hash-mismatch",
    ],
)
def test_publication_requires_authoritative_analysis_journals(
    tmp_path: Path, case: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    write_execution_fixture(execution, identity)
    match = execution / "matches/A"
    request_path = match / "analysis_requests.jsonl"
    result_path = match / "analysis_results.jsonl"
    decision_path = match / "decisions.jsonl"
    manifest_path = match / "match_manifest.json"
    requests = [json.loads(line) for line in request_path.read_text().splitlines()]
    results = [json.loads(line) for line in result_path.read_text().splitlines()]
    decisions = [json.loads(line) for line in decision_path.read_text().splitlines()]

    if case == "missing-request":
        requests.pop()
    elif case == "missing-result":
        results.pop()
    elif case == "duplicate":
        requests.insert(1, dict(requests[0]))
        results.insert(1, dict(results[0]))
    elif case == "reordered":
        requests[0], requests[1] = requests[1], requests[0]
        results[0], results[1] = results[1], results[0]
    elif case == "wrong-decision-association":
        requests[0]["decision_ordinal"] = requests[1]["decision_ordinal"]
        results[0]["decision_ordinal"] = results[1]["decision_ordinal"]
    elif case in {"wrong-engine", "wrong-seat", "wrong-gnuid", "wrong-type"}:
        key, value = {
            "wrong-engine": ("engine", "gnu"),
            "wrong-seat": ("physical_seat", "X"),
            "wrong-gnuid": ("gnuid", decisions[-1]["gnuid"]),
            "wrong-type": ("decision_type", "cube"),
        }[case]
        requests[0][key] = value
        results[0][key] = value
    elif case == "malformed-result":
        results[0]["returned_result"] = "malformed"
    elif case == "wrong-configured-depth":
        results[0]["returned_result"]["engine"]["analysis_setting"] = "2ply"
        decisions[0]["engine_kit_result"]["engine"]["analysis_setting"] = "2ply"
    elif case == "invalid-actual-depth":
        raw = results[0]["returned_result"]
        raw["checker_decision"]["actual_ply"] = -1
        raw["checker_decision"]["candidates"][0]["actual_ply"] = -1
        validated = decisions[0]["engine_kit_result"]
        validated["checker_decision"] = raw["checker_decision"]
        validated["campaign_depth_evidence"]["recommended_actual_ply"] = -1
        validated["campaign_depth_evidence"]["candidate_actual_plies"] = [-1]
    elif case in {"cross-side", "cross-game", "cross-pair"}:
        key, value = {
            "cross-side": ("match_side", "B"),
            "cross-game": ("game_number", 2),
            "cross-pair": ("pair_id", "pair-" + "0" * 24),
        }[case]
        requests[0][key] = value
        results[0][key] = value
    elif case == "missing-raw-source":
        results[0]["returned_result"].pop("raw_source")
        decisions[0]["engine_kit_result"].pop("raw_source")
    elif case == "malformed-candidates":
        raw = results[0]["returned_result"]
        raw["checker_decision"]["candidates"].append(
            dict(raw["checker_decision"]["candidates"][0])
        )
        decisions[0]["engine_kit_result"]["checker_decision"] = raw["checker_decision"]
        decisions[0]["engine_kit_result"]["campaign_depth_evidence"][
            "candidate_actual_plies"
        ].append(raw["checker_decision"]["actual_ply"])
    elif case == "wrong-manifest-path":
        manifest = json.loads(manifest_path.read_text())
        manifest["analysis_request_evidence"] = "wrong.jsonl"
        write_json(manifest_path, manifest)
    elif case == "manifest-file-hash-mismatch":
        requests[0]["engine"] = "gnu"

    request_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in requests),
        encoding="utf-8",
    )
    result_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in results),
        encoding="utf-8",
    )
    decision_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in decisions),
        encoding="utf-8",
    )
    if case not in {"wrong-manifest-path", "manifest-file-hash-mismatch"}:
        manifest = json.loads(manifest_path.read_text())
        for path in (request_path, result_path, decision_path):
            manifest["output_sha256"][str(path.relative_to(match))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        write_json(manifest_path, manifest)

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    with pytest.raises(MatchExecutionError, match="analysis|manifest"):
        publish_pair(
            execution, artifact_root, config, identity, publication_common(),
            {"attempt_count": 1, "transitions": []},
        )


@pytest.mark.parametrize(
    "substitution", ["physical-seat", "engine", "stream", "cross-side"],
)
def test_publication_rejects_equal_dice_collision_identity_substitution(
    tmp_path: Path, substitution: str,
) -> None:
    config = load_campaign_config(CONFIG)
    identity = pair_identity(config, 1)
    execution = tmp_path / "execution"
    write_execution_fixture(execution, identity)
    match = execution / "matches/A"
    consumption_path = match / "dice/seat_dice_consumption.jsonl"
    records = [json.loads(line) for line in consumption_path.read_text().splitlines()]
    target = next(record for record in records if record["prompt_type"] == "checker")
    other_seat = "X" if target["physical_seat"] == "O" else "O"
    collision_side = "B" if substitution == "cross-side" else "A"
    collision_seed = namespace_seed(identity.base_seed, collision_side)
    collision = next(
        (index, row) for index in range(1, 50001)
        if (row := dice_record(
            collision_seed, 1, 7, target["game_number"], other_seat, index
        ))["die1"] == target["die1"] and row["die2"] == target["die2"]
    )
    collision_index, _ = collision
    assert other_seat != target["physical_seat"]
    if substitution == "physical-seat":
        target["physical_seat"] = other_seat
    elif substitution == "engine":
        target["engine"] = "gnu" if target["engine"] == "sage" else "sage"
    elif substitution == "stream":
        target["stream_id"] = stream_id(
            collision_seed, target["game_number"], other_seat
        )
        target["stream_path"] = f"game_{target['game_number']:03d}_seat_{other_seat}.csv"
    else:
        target.update({
            "namespace": "B", "namespace_seed": collision_seed,
            "pair_member": "B", "match_side": "B", "physical_seat": other_seat,
            "engine": {"O": "gnu", "X": "sage"}[other_seat],
            "roll_index": collision_index,
            "stream_id": stream_id(collision_seed, target["game_number"], other_seat),
            "stream_path": f"game_{target['game_number']:03d}_seat_{other_seat}.csv",
        })
    consumption_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    dice_manifest_path = match / "dice/seat_dice_manifest.json"
    dice_manifest = json.loads(dice_manifest_path.read_text())
    dice_manifest["consumption"]["sha256"] = hashlib.sha256(
        consumption_path.read_bytes()
    ).hexdigest()
    write_json(dice_manifest_path, dice_manifest)
    manifest_path = match / "match_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for path in (consumption_path, dice_manifest_path):
        manifest["output_sha256"][str(path.relative_to(match))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    write_json(manifest_path, manifest)

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    with pytest.raises(MatchExecutionError):
        publish_pair(
            execution, artifact_root, config, identity, publication_common(),
            {"attempt_count": 1, "transitions": []},
        )


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
            sgf_path.read_text(encoding="utf-8").replace("RE[W+6R]", "RE[W+5R]"),
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
        terminal_record = next(
            record for record in records
            if record["transition_evidence"]["terminal_event"] is not None
        )
        terminal_record["transition_evidence"]["terminal_event"]["points"] += 1
        decisions.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
    elif case == "match-manifest-result-mismatch":
        manifest_path = match / "match_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["native_evidence"]["games"][0]["points"] = 3
        write_json(manifest_path, manifest)
    elif case == "same-result-different-checker-move":
        move = re.search(r"[1-6]{2}: (\d+)/(\d+)", text)
        assert move is not None
        replacement = f"{move.group(1)}/{1 if move.group(2) != '1' else 2}"
        text_path.write_text(text[:move.start(1)] + replacement + text[move.end(2):], encoding="utf-8")
    elif case == "same-result-different-dice-sequence":
        dice = re.search(r"([1-6])([1-6]):", text)
        assert dice is not None
        changed = "1" if dice.group(1) != "1" and dice.group(2) != "1" else "6"
        text_path.write_text(text[:dice.start(1)] + changed + text[dice.end(1):], encoding="utf-8")
    elif case == "same-result-different-cube-action":
        text_path.write_text(text.replace("Doubles => 2                Takes", "Doubles => 2                Drops", 1), encoding="utf-8")
    elif case == "same-result-different-terminal-action":
        checker = re.search(r"[1-6]{2}: \d+/\d+", text)
        assert checker is not None
        text_path.write_text(
            text[:checker.start()] + "Drops" + text[checker.end():], encoding="utf-8"
        )
    elif case == "root-only-sgf-with-result":
        trees = sgf_path.read_text(encoding="utf-8").splitlines(keepends=True)
        trees[0] = re.sub(r";[WB]\[[^\n]*", ")\n", trees[0], count=1)
        sgf_path.write_text("".join(trees), encoding="utf-8")
    elif case == "result-only-text-with-result":
        first_result = text.index("      Wins 2 points")
        first_actions = text.index("  1)", text.index(" Game 1"))
        text_path.write_text(text[:first_actions] + text[first_result:], encoding="utf-8")
    elif case == "reordered-actions":
        row = re.search(r"(?m)^(\s*1\) )(.{27}) (.+)$", text)
        assert row is not None
        swapped = row.group(1) + f"{row.group(3):<27} " + row.group(2).strip()
        text_path.write_text(text[:row.start()] + swapped + text[row.end():], encoding="utf-8")
    elif case == "missing-move":
        move = re.search(r"[1-6]{2}: \d+/\d+", text)
        assert move is not None
        text_path.write_text(text[:move.start()] + text[move.end():], encoding="utf-8")
    elif case == "extra-move":
        move = re.search(r"[1-6]{2}: \d+/\d+", text)
        assert move is not None
        text_path.write_text(text[:move.end()] + " 13/10" + text[move.end():], encoding="utf-8")
    elif case == "action-belongs-to-wrong-game":
        game_two_action = re.search(r"[1-6]{2}: \d+/\d+", text[game_2:])
        game_one_action = re.search(r"[1-6]{2}: \d+/\d+", text)
        assert game_two_action is not None and game_one_action is not None
        transplanted = game_two_action.group(0)
        text_path.write_text(
            text[:game_one_action.start()] + transplanted + text[game_one_action.end():],
            encoding="utf-8",
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
