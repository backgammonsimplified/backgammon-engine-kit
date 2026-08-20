from __future__ import annotations

from pathlib import Path

import pytest

from runner.sage_gnu_campaign.cli import parser
from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.preflight import PreflightError, validate_roots


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"


def test_runtime_and_artifact_defaults_are_unset_and_cross_campaign_roots_are_denied(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    assert config.data["runtime_policy"]["default_root"] is None
    assert config.data["artifact_policy"]["default_root"] is None
    repository = tmp_path / "benchmarker"
    with pytest.raises(PreflightError, match="forbidden cross-campaign"):
        validate_roots(
            config,
            repository,
            tmp_path / "canonical-output" / "runtime",
            tmp_path / "campaign-artifacts",
        )
    with pytest.raises(PreflightError, match="forbidden cross-campaign"):
        validate_roots(
            config,
            repository,
            tmp_path / "canonical" / "runtime",
            tmp_path / "campaign-artifacts",
        )


def test_campaign_runner_has_no_ingestion_or_writer_action() -> None:
    commands = set(parser()._subparsers._group_actions[0].choices)
    assert commands == {"plan", "status", "bootstrap", "preflight", "run"}
    source_root = REPO / "runner/sage_gnu_campaign"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))
    assert "CanonicalParquetWriter" not in source
    assert "canonical_ingestion" not in source
    assert "writer lease" not in source.lower()


def test_cli_exposes_no_experiment_setting_overrides() -> None:
    help_text = parser().format_help()
    for forbidden in (
        "--match-length",
        "--sage-checker",
        "--sage-cube",
        "--gnu-checker",
        "--gnu-cube",
        "--dice-seed",
        "--move-filter",
        "--engine-kit-root",
    ):
        assert forbidden not in help_text
