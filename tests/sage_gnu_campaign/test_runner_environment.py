from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.environment import (
    RunnerEnvironmentError,
    _validate_import_location,
    bootstrap_runner_environment,
    runner_venv,
    runner_workspace,
    verify_runner_environment,
)
from runner.sage_gnu_campaign.manifests import common_manifest, sha256_file, write_json
from runner.sage_gnu_campaign.preflight import validate_roots


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"


def test_runner_environment_is_beneath_runtime_and_outside_checkout_and_artifacts(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    repository = tmp_path / "campaign"
    runtime = tmp_path / "runtime"
    artifacts = tmp_path / "artifacts"
    validate_roots(config, repository, runtime, artifacts)
    workspace = runner_workspace(config, runtime)
    environment = runner_venv(config, runtime)
    assert workspace.is_relative_to(runtime)
    assert environment.is_relative_to(runtime)
    assert not environment.is_relative_to(repository)
    assert not environment.is_relative_to(artifacts)


def test_imported_engine_kit_must_be_isolated_under_runner_environment(tmp_path: Path) -> None:
    environment = tmp_path / "runtime/campaign/runner-workspace/.venv"
    isolated = {
        "prefix": str(environment),
        "module_file": str(environment / "lib/python3.11/site-packages/backgammon_engine_kit/__init__.py"),
        "dist_info": str(environment / "lib/python3.11/site-packages/backgammon_engine_kit-0.4.0.dist-info"),
        "direct_url": None,
    }
    _validate_import_location(isolated, environment)
    leaked = {**isolated, "module_file": str(tmp_path / "elsewhere/backgammon_engine_kit/__init__.py")}
    with pytest.raises(RunnerEnvironmentError, match="does not resolve"):
        _validate_import_location(leaked, environment)
    editable = {**isolated, "direct_url": {"url": "file:///wheel", "dir_info": {"editable": True}}}
    with pytest.raises(RunnerEnvironmentError, match="editable"):
        _validate_import_location(editable, environment)


def _environment_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, Path, Path]:
    import runner.sage_gnu_campaign.environment as module

    config = load_campaign_config(CONFIG)
    repository = tmp_path / "repository"
    runtime = tmp_path / "runtime"
    workspace = runner_workspace(config, runtime)
    environment = workspace / ".venv"
    site = environment / "lib/python3.11/site-packages"
    module_file = site / "backgammon_engine_kit/__init__.py"
    dist_info = site / "backgammon_engine_kit-0.4.0.dist-info"
    python = environment / "bin/python"
    wheel = workspace / "wheelhouse/backgammon_engine_kit-0.4.0-py3-none-any.whl"
    lock = workspace / "requirements-production.lock"
    authority_lock = tmp_path / "authority.lock"
    for path in (module_file, dist_info / "RECORD", python, wheel, lock, authority_lock):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"identity\n")
    freeze = b"backgammon-engine-kit==0.4.0\n"
    (workspace / "requirements.freeze.txt").write_bytes(freeze)
    observed = {
        "prefix": str(environment),
        "executable": str(python),
        "module_file": str(module_file),
        "dist_info": str(dist_info),
        "distribution_name": "backgammon-engine-kit",
        "distribution_version": "0.4.0",
        "record_sha256": sha256_file(dist_info / "RECORD"),
        "direct_url": None,
    }
    config.data["engine_kit"]["release"]["wheel_sha256"] = sha256_file(wheel)
    config.data["engine_kit"]["production_dependency_lock"]["sha256"] = sha256_file(authority_lock)
    monkeypatch.setattr(module, "_probe_subprocess", lambda _: observed)
    monkeypatch.setattr(module, "_freeze", lambda _: freeze)
    monkeypatch.setattr(module, "_dependency_lock", lambda *_: authority_lock)
    manifest = {
        "schema_version": "sage-gnu-runner-environment-v2",
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "engine_kit_source_commit": config.data["engine_kit"]["source_commit"],
        "engine_kit_release_commit": config.data["engine_kit"]["release_commit"],
        "engine_kit_package": {
            "distribution_name": "backgammon-engine-kit",
            "distribution_version": "0.4.0",
            "wheel_filename": wheel.name,
            "wheel_sha256": sha256_file(wheel),
            "wheel_source_url": module._release_wheel_url(config),
            "record_sha256": observed["record_sha256"],
            "installation_mode": "public-release-wheel-plus-hash-lock",
        },
        "dependency_lock": {
            "filename": lock.name,
            "sha256": sha256_file(lock),
            "install_mode": "pip-install-require-hashes",
        },
        "python": {"executable_sha256": sha256_file(python)},
        "freeze_sha256": hashlib.sha256(freeze).hexdigest(),
    }
    write_json(workspace / "environment_manifest.json", manifest)
    return config, repository, runtime


def test_release_and_installed_package_identity_mismatches_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, repository, runtime = _environment_fixture(tmp_path, monkeypatch)
    manifest_path = runner_workspace(config, runtime) / "environment_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine_kit_release_commit"] = "0" * 40
    write_json(manifest_path, manifest)
    with pytest.raises(RunnerEnvironmentError, match="release_commit"):
        verify_runner_environment(config, repository, runtime, require_active=False)
    manifest["engine_kit_release_commit"] = config.data["engine_kit"]["release_commit"]
    manifest["engine_kit_package"]["record_sha256"] = "0" * 64
    write_json(manifest_path, manifest)
    with pytest.raises(RunnerEnvironmentError, match="RECORD"):
        verify_runner_environment(config, repository, runtime, require_active=False)


def test_existing_matching_environment_reconciles_idempotently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, repository, runtime = _environment_fixture(tmp_path, monkeypatch)
    result = bootstrap_runner_environment(config, repository, runtime)
    assert result["status"] == "reconciled"
    assert result["runner_environment"]["freeze_sha256"]
    assert len(result["runner_environment"]["environment_manifest_sha256"]) == 64


def test_conflicting_existing_runner_workspace_is_preserved_and_rejected(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    runtime = tmp_path / "runtime"
    workspace = runner_workspace(config, runtime)
    workspace.mkdir(parents=True)
    sentinel = workspace / "operator-review.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    with pytest.raises(RunnerEnvironmentError, match="absent or incomplete"):
        bootstrap_runner_environment(config, tmp_path / "repository", runtime)
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_bootstrap_installs_hash_lock_before_exact_release_wheel() -> None:
    source = (REPO / "runner/sage_gnu_campaign/environment.py").read_text(encoding="utf-8")
    assert "--require-hashes" in source
    assert "--no-deps" in source
    assert source.index("--require-hashes") < source.index("--no-deps")
    assert '"pip", "wheel"' not in source
    assert "git archive" not in source


def test_environment_identity_fields_enter_common_manifests() -> None:
    config = load_campaign_config(CONFIG)
    environment = {
        "engine_kit_source_commit": config.data["engine_kit"]["source_commit"],
        "engine_kit_release_commit": config.data["engine_kit"]["release_commit"],
        "engine_kit_package": {"wheel_sha256": "w" * 64, "record_sha256": "r" * 64},
        "dependency_lock": {"sha256": "l" * 64},
        "python": {"executable_sha256": "p" * 64},
        "freeze_sha256": "f" * 64,
    }
    common = common_manifest(
        config,
        {"commit": "b" * 40},
        {
            "source_commit": config.data["engine_kit"]["source_commit"],
            "release_commit": config.data["engine_kit"]["release_commit"],
        },
        {},
        environment,
    )
    assert common["runner_environment"] == environment
    assert common["configured_profile"]["sage_threads"] == 1
    assert common["configured_profile"]["gnu_threads"] == 1
