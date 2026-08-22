from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.environment import (
    RunnerEnvironmentError,
    _environment_content_sha256,
    _validate_import_location,
    bootstrap_runner_environment,
    durably_establish_runner_workspace,
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
        "directory_durability": {
            "protocol": "runtime-root-directory-fsync-v1",
            "runtime_root": module.path_identity(runtime, "runner-runtime-root"),
            "runner_workspace": module.path_identity(
                workspace, "campaign-runner-workspace"
            ),
        },
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
        "environment_content_sha256": _environment_content_sha256(environment),
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


def test_existing_legacy_environment_records_established_durable_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, repository, runtime = _environment_fixture(tmp_path, monkeypatch)
    manifest_path = runner_workspace(config, runtime) / "environment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("directory_durability")
    write_json(manifest_path, manifest)
    result = bootstrap_runner_environment(config, repository, runtime)
    assert result["status"] == "reconciled"
    assert result["runner_environment"]["directory_durability"]["protocol"] == (
        "runtime-root-directory-fsync-v1"
    )


def test_runner_workspace_creation_is_durably_anchored_at_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.environment as module

    config = load_campaign_config(CONFIG)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    events: list[Path] = []
    real_fsync = module.fsync_directory

    def recording_fsync(path: Path) -> None:
        events.append(Path(path))
        real_fsync(path)

    monkeypatch.setattr(module, "fsync_directory", recording_fsync)
    workspace = durably_establish_runner_workspace(config, runtime)
    campaign_directory = runtime / config.campaign_id
    assert workspace == campaign_directory / "runner-workspace"
    assert events == [
        runtime,
        campaign_directory,
        runtime,
        workspace,
        campaign_directory,
    ]


def test_bootstrap_durably_creates_a_missing_operator_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.environment as module

    config = load_campaign_config(CONFIG)
    runtime = tmp_path / "runtime"
    events: list[Path] = []
    real_fsync = module.fsync_directory

    def recording_fsync(path: Path) -> None:
        events.append(Path(path))
        real_fsync(path)

    monkeypatch.setattr(module, "fsync_directory", recording_fsync)
    workspace = durably_establish_runner_workspace(config, runtime)
    campaign_directory = runtime / config.campaign_id
    assert events == [
        runtime,
        tmp_path,
        campaign_directory,
        runtime,
        workspace,
        campaign_directory,
    ]


def test_new_bootstrap_continues_after_durable_workspace_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runner.sage_gnu_campaign.environment as module

    config = load_campaign_config(CONFIG)
    repository = tmp_path / "repository"
    runtime = tmp_path / "runtime"
    authority_lock = tmp_path / "authority.lock"
    authority_lock.write_bytes(b"locked\n")
    wheel_bytes = b"public wheel\n"
    config.data["engine_kit"]["production_dependency_lock"]["sha256"] = sha256_file(authority_lock)
    config.data["engine_kit"]["release"]["wheel_sha256"] = hashlib.sha256(wheel_bytes).hexdigest()

    class FakeEnvBuilder:
        def __init__(self, **_: object) -> None:
            pass

        def create(self, root: Path) -> None:
            python = Path(root) / "bin/python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python\n")

    observed = {
        "distribution_name": "backgammon-engine-kit",
        "distribution_version": "0.4.0",
        "record_sha256": "r" * 64,
        "executable": "python",
    }
    monkeypatch.setattr(module, "_dependency_lock", lambda *_: authority_lock)
    monkeypatch.setattr(module, "_download_release_wheel", lambda _: wheel_bytes)
    monkeypatch.setattr(module.venv, "EnvBuilder", FakeEnvBuilder)
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: SimpleNamespace(stdout="Python 3.11\n", stderr=""))
    monkeypatch.setattr(module, "_freeze", lambda _: b"frozen\n")
    monkeypatch.setattr(module, "_probe_subprocess", lambda _: observed)
    monkeypatch.setattr(module, "_validate_import_location", lambda *_: None)

    def verify_after_creation(*_: object, **__: object) -> dict[str, object]:
        manifest = runner_workspace(config, runtime) / "environment_manifest.json"
        assert manifest.is_file()
        return json.loads(manifest.read_text(encoding="utf-8"))

    monkeypatch.setattr(module, "verify_runner_environment", verify_after_creation)
    result = bootstrap_runner_environment(config, repository, runtime)
    assert result["status"] == "created"
    assert result["runner_environment"]["directory_durability"]["protocol"] == (
        "runtime-root-directory-fsync-v1"
    )


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


def test_runner_environment_content_mutation_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, repository, runtime = _environment_fixture(tmp_path, monkeypatch)
    environment = runner_venv(config, runtime)
    target = environment / "lib/python3.11/site-packages/backgammon_engine_kit/__init__.py"
    target.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RunnerEnvironmentError, match="content inventory"):
        verify_runner_environment(config, repository, runtime, require_active=False)


def test_unrecorded_sourceless_bytecode_outside_pycache_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, repository, runtime = _environment_fixture(tmp_path, monkeypatch)
    environment = runner_venv(config, runtime)
    (environment / "lib/python3.11/site-packages/unrecorded.pyc").write_bytes(b"sourceless-bytecode")
    with pytest.raises(RunnerEnvironmentError, match="content inventory"):
        verify_runner_environment(config, repository, runtime, require_active=False)


def test_interpreter_cache_bytecode_under_pycache_remains_volatile(tmp_path: Path) -> None:
    environment = tmp_path / ".venv"
    cache = environment / "lib/python3.11/site-packages/package/__pycache__"
    cache.mkdir(parents=True)
    before = _environment_content_sha256(environment)
    (cache / "module.cpython-311.pyc").write_bytes(b"volatile-cache")
    assert _environment_content_sha256(environment) == before


def test_gnu_native_output_roots_reject_command_unsafe_paths(tmp_path: Path) -> None:
    config = load_campaign_config(CONFIG)
    repository = tmp_path / "repo"
    with pytest.raises(Exception, match="unsafe characters"):
        validate_roots(config, repository, tmp_path / "runtime with space", tmp_path / "artifacts")
