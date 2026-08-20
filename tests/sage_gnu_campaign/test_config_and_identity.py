from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.sage_gnu_campaign.config import ConfigurationError, GNU_NORMAL_V1, load_campaign_config
from runner.sage_gnu_campaign.identity import all_pair_identities, pair_identity


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"


def test_campaign_config_is_deterministic_hashable_and_schema_versioned() -> None:
    first = load_campaign_config(CONFIG)
    second = load_campaign_config(CONFIG)
    assert first.content_sha256 == second.content_sha256
    assert len(first.content_sha256) == 64
    assert first.schema_version == "sage-gnu-campaign-config-v1"
    assert first.path == CONFIG.resolve()


def test_exact_profile_is_frozen_and_old_crossed_profile_is_absent() -> None:
    config = load_campaign_config(CONFIG).data
    assert config["match"]["length_points"] == 7
    assert config["engines"]["sage"]["checker_configured_target"] == "4ply"
    assert config["engines"]["sage"]["cube_configured_target"] == "3ply"
    assert config["engines"]["gnu"]["checker_configured_target"] == "3ply"
    assert config["engines"]["gnu"]["cube_configured_target"] == "2ply"
    assert config["engines"]["gnu"]["checker_move_filter_identity"] == GNU_NORMAL_V1
    assert config["engines"]["sage"]["threads"] == 1
    assert config["engines"]["gnu"]["threads"] == 1
    serialized = CONFIG.read_text(encoding="utf-8")
    assert '"sage"' in serialized and '"gnu"' in serialized
    assert '"cube_configured_target": "4ply"' not in serialized
    assert '"checker_configured_target": "3ply"' in serialized


def test_changed_profile_is_rejected(tmp_path: Path) -> None:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    data["engines"]["sage"]["checker_configured_target"] = "3ply"
    data["engines"]["sage"]["cube_configured_target"] = "3ply"
    data["engines"]["gnu"]["checker_configured_target"] = "4ply"
    data["engines"]["gnu"]["cube_configured_target"] = "4ply"
    changed = tmp_path / "campaign.json"
    changed.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_campaign_config(changed)


def test_engine_kit_source_and_release_identity_fields_are_pinned() -> None:
    kit = load_campaign_config(CONFIG).data["engine_kit"]
    assert kit["repository"] == "backgammonsimplified/backgammon-engine-kit"
    assert kit["source_commit"] == "f87c69b10efa707f52aa1e42c74808d9b3bc109f"
    assert kit["base_commit"] == "ab42f8186c5b04ca965a268bdc203179a7a669d8"
    assert kit["release_commit"] == "f13446140ea06f9dc1ef51d4b6b0c83c5a46237d"
    assert kit["release"]["tag"] == "v0.4.0"
    assert len(kit["release"]["wheel_sha256"]) == 64
    assert len(kit["release"]["sdist_sha256"]) == 64
    assert len(kit["production_dependency_lock"]["sha256"]) == 64


def test_pair_id_and_base_seed_are_stable_and_bounded() -> None:
    config = load_campaign_config(CONFIG)
    first = pair_identity(config, 1)
    assert first == pair_identity(config, 1)
    assert first.pair_id == "pair-000001-191bd73684228b92"
    assert first.base_seed == "sha256:4ecb9ad6b75ca0e48096bad7538fd8fb6a038ebdd9ad3cc8fe27619cfec89741"
    assert len(all_pair_identities(config)) == 10
    assert pair_identity(config, 2) != first
    with pytest.raises(ValueError):
        pair_identity(config, 11)


def test_a_b_physical_seat_reversal_is_exact() -> None:
    members = load_campaign_config(CONFIG).data["match"]["members"]
    assert members["A"] == {"sage_physical_seat": "O", "gnu_physical_seat": "X"}
    assert members["B"] == {"sage_physical_seat": "X", "gnu_physical_seat": "O"}
