"""Campaign-owned runner environment built only from committed public release assets."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path
from typing import Any

from .config import CampaignConfig
from .manifests import fsync_directory, path_identity, sha256_file, write_bytes_atomic, write_json


ENVIRONMENT_SCHEMA = "sage-gnu-runner-environment-v2"
LEGACY_PUBLIC_RUNNER_AUTHORITY = "96cbed94dbf6435ab54ff23de4ccdade7da640af"
LEGACY_MIGRATION_PROTOCOL = "pinned-public-artifact-rebuild-v1"


class RunnerEnvironmentError(RuntimeError):
    """The campaign runner environment is missing, mutable, or conflicts with authority."""


def runner_workspace(config: CampaignConfig, runtime_root: Path) -> Path:
    return Path(runtime_root).resolve() / config.campaign_id / "runner-workspace"


def runner_venv(config: CampaignConfig, runtime_root: Path) -> Path:
    return runner_workspace(config, runtime_root) / ".venv"


def pair_attempt_workspace(config: CampaignConfig, runtime_root: Path, pair_id: str, attempt: int) -> Path:
    return runner_workspace(config, runtime_root) / pair_id / f"attempt-{attempt}"


def _durably_create_directories(anchor: Path, target: Path) -> None:
    durable_anchor = Path(anchor).resolve(strict=True)
    requested_target = Path(target).absolute()
    if (
        durable_anchor.is_symlink()
        or not durable_anchor.is_dir()
        or not requested_target.is_relative_to(durable_anchor)
    ):
        raise RunnerEnvironmentError("runner workspace is not below a durable directory anchor")
    current = durable_anchor
    for component in requested_target.relative_to(durable_anchor).parts:
        candidate = current / component
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_dir():
                raise RunnerEnvironmentError(
                    f"runner workspace hierarchy conflicts with non-directory: {candidate}"
                )
        else:
            candidate.mkdir()
            fsync_directory(candidate)
            fsync_directory(current)
        current = candidate


def durably_establish_runner_workspace(
    config: CampaignConfig, runtime_root: Path
) -> Path:
    """Durably link the production runner workspace to the operator runtime root."""
    requested_runtime = Path(runtime_root).resolve(strict=False)
    runtime_existed = requested_runtime.exists()
    existing_anchor = requested_runtime
    while not existing_anchor.exists():
        parent = existing_anchor.parent
        if parent == existing_anchor:
            raise RunnerEnvironmentError("runtime root has no existing durable ancestor")
        existing_anchor = parent
    _durably_create_directories(existing_anchor, requested_runtime)
    runtime = requested_runtime.resolve(strict=True)
    if runtime_existed:
        fsync_directory(runtime)
    workspace = runtime / config.campaign_id / "runner-workspace"
    workspace_existed = workspace.exists()
    _durably_create_directories(runtime, workspace)
    if workspace_existed:
        campaign_directory = workspace.parent
        if (
            campaign_directory.is_symlink()
            or workspace.is_symlink()
            or not campaign_directory.is_dir()
            or not workspace.is_dir()
        ):
            raise RunnerEnvironmentError("runner workspace hierarchy is not a directory chain")
        fsync_directory(workspace)
        fsync_directory(campaign_directory)
        fsync_directory(runtime)
    return workspace.resolve(strict=True)


def _directory_durability_identity(
    config: CampaignConfig, runtime_root: Path
) -> dict[str, Any]:
    runtime = Path(runtime_root).resolve()
    return {
        "protocol": "runtime-root-directory-fsync-v1",
        "runtime_root": path_identity(runtime, "runner-runtime-root"),
        "runner_workspace": path_identity(
            runner_workspace(config, runtime), "campaign-runner-workspace"
        ),
    }


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RunnerEnvironmentError(detail)
    return result


def _freeze(python: Path) -> bytes:
    return _run([str(python), "-m", "pip", "freeze", "--all"]).stdout.encode("utf-8")


def _environment_content_sha256(environment_root: Path) -> str:
    root = Path(environment_root).resolve()
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.suffix == ".pyc" and relative.parent.name == "__pycache__":
            continue
        if path.is_symlink():
            entries.append(f"L\0{relative.as_posix()}\0{os.readlink(path)}")
        elif path.is_file():
            entries.append(f"F\0{relative.as_posix()}\0{sha256_file(path)}")
    return hashlib.sha256(("\n".join(entries) + "\n").encode("utf-8")).hexdigest()


def _distribution_identity() -> dict[str, Any]:
    package = importlib.import_module("backgammon_engine_kit")
    distribution = importlib.metadata.distribution("backgammon-engine-kit")
    dist_info = Path(distribution._path).resolve()  # type: ignore[attr-defined]
    record = dist_info / "RECORD"
    if not record.is_file():
        raise RunnerEnvironmentError("installed Engine Kit distribution lacks RECORD identity")
    direct_url_path = dist_info / "direct_url.json"
    direct_url = json.loads(direct_url_path.read_text(encoding="utf-8")) if direct_url_path.is_file() else None
    return {
        "distribution_name": distribution.metadata["Name"],
        "distribution_version": distribution.version,
        "module_file": str(Path(package.__file__).resolve()),
        "dist_info": str(dist_info),
        "record_sha256": sha256_file(record),
        "direct_url": direct_url,
    }


def _probe_subprocess(python: Path) -> dict[str, Any]:
    script = """
import hashlib
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path
package = importlib.import_module("backgammon_engine_kit")
distribution = importlib.metadata.distribution("backgammon-engine-kit")
dist_info = Path(distribution._path).resolve()
record = dist_info / "RECORD"
direct_url_path = dist_info / "direct_url.json"
value = {
    "distribution_name": distribution.metadata["Name"],
    "distribution_version": distribution.version,
    "module_file": str(Path(package.__file__).resolve()),
    "dist_info": str(dist_info),
    "record_sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
    "direct_url": json.loads(direct_url_path.read_text()) if direct_url_path.is_file() else None,
    "prefix": sys.prefix,
    "executable": sys.executable,
}
print(json.dumps(value, sort_keys=True))
"""
    result = _run([str(python), "-c", script])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerEnvironmentError("runner environment identity probe was malformed") from exc


def _validate_import_location(observed: dict[str, Any], environment_root: Path) -> None:
    environment_root = Path(environment_root).resolve()
    prefix = Path(observed["prefix"]).resolve()
    module_file = Path(observed["module_file"]).resolve()
    dist_info = Path(observed["dist_info"]).resolve()
    if prefix != environment_root:
        raise RunnerEnvironmentError("active Python is not the campaign-owned runner environment")
    if not module_file.is_relative_to(environment_root) or not dist_info.is_relative_to(environment_root):
        raise RunnerEnvironmentError("imported Engine Kit does not resolve under the runner environment")
    direct_url = observed.get("direct_url")
    if direct_url:
        if not isinstance(direct_url, dict):
            raise RunnerEnvironmentError("Engine Kit direct installation identity is malformed")
        if direct_url.get("dir_info", {}).get("editable") is True:
            raise RunnerEnvironmentError("editable Engine Kit installation is forbidden")


def _release_wheel_url(config: CampaignConfig) -> str:
    kit = config.data["engine_kit"]
    repository = kit["repository"]
    tag = kit["release"]["tag"]
    filename = kit["release"]["wheel_filename"]
    return f"https://raw.githubusercontent.com/{repository}/{tag}/release-assets/v0.4.0/{filename}"


def _dependency_lock(config: CampaignConfig, repository: Path) -> Path:
    lock = Path(repository).resolve() / config.data["engine_kit"]["production_dependency_lock"]["path"]
    expected = config.data["engine_kit"]["production_dependency_lock"]["sha256"]
    if not lock.is_file() or sha256_file(lock) != expected:
        raise RunnerEnvironmentError("committed Engine Kit production dependency lock identity mismatch")
    return lock


def _download_release_wheel(config: CampaignConfig) -> bytes:
    request = urllib.request.Request(_release_wheel_url(config), headers={"User-Agent": "sage-gnu-benchmark-runner/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    except Exception as exc:
        raise RunnerEnvironmentError("unable to retrieve the pinned public Engine Kit release wheel") from exc
    expected = config.data["engine_kit"]["release"]["wheel_sha256"]
    if hashlib.sha256(payload).hexdigest() != expected:
        raise RunnerEnvironmentError("downloaded Engine Kit release wheel identity mismatch")
    return payload


def _read_environment_manifest(workspace: Path) -> tuple[dict[str, Any], str]:
    manifest_path = workspace / "environment_manifest.json"
    try:
        payload = manifest_path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerEnvironmentError("campaign runner environment manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise RunnerEnvironmentError("campaign runner environment manifest is malformed")
    return manifest, hashlib.sha256(payload).hexdigest()


def _validate_legacy_migration_authority(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    manifest: dict[str, Any],
) -> None:
    """Authorize only the exact public-runner manifest shape shipped at 96cbed9."""
    if manifest.get("schema_version") != ENVIRONMENT_SCHEMA:
        raise RunnerEnvironmentError("runner environment schema is not explicitly migratable")
    legacy_fields = {
        "schema_version",
        "campaign_id",
        "campaign_configuration_sha256",
        "engine_kit_source_commit",
        "engine_kit_release_commit",
        "engine_kit_package",
        "dependency_lock",
        "python",
        "freeze_file",
        "freeze_sha256",
        "environment_path_identity",
    }
    if set(manifest) != legacy_fields:
        raise RunnerEnvironmentError("runner environment is not the exact migratable public manifest")
    kit = config.data["engine_kit"]
    expected = {
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "engine_kit_source_commit": kit["source_commit"],
        "engine_kit_release_commit": kit["release_commit"],
        "freeze_file": "requirements.freeze.txt",
        "environment_path_identity": path_identity(
            runner_venv(config, runtime_root), "campaign-runner-venv"
        ),
    }
    conflicts = [key for key, value in expected.items() if manifest.get(key) != value]
    if conflicts:
        raise RunnerEnvironmentError(
            "legacy runner environment authority mismatch: " + ", ".join(conflicts)
        )

    package = manifest.get("engine_kit_package")
    expected_package = {
        "distribution_name": "backgammon-engine-kit",
        "distribution_version": "0.4.0",
        "wheel_filename": kit["release"]["wheel_filename"],
        "wheel_sha256": kit["release"]["wheel_sha256"],
        "wheel_source_url": _release_wheel_url(config),
        "installation_mode": "public-release-wheel-plus-hash-lock",
    }
    if (
        not isinstance(package, dict)
        or set(package) != {*expected_package, "record_sha256"}
        or any(package.get(key) != value for key, value in expected_package.items())
        or not isinstance(package.get("record_sha256"), str)
        or len(package["record_sha256"]) != 64
    ):
        raise RunnerEnvironmentError("legacy Engine Kit package identity mismatch")

    lock_authority = _dependency_lock(config, repository)
    lock = manifest.get("dependency_lock")
    expected_lock = {
        "filename": "requirements-production.lock",
        "sha256": kit["production_dependency_lock"]["sha256"],
        "install_mode": "pip-install-require-hashes",
    }
    if not isinstance(lock, dict) or lock != expected_lock:
        raise RunnerEnvironmentError("legacy dependency lock authority mismatch")
    workspace = runner_workspace(config, runtime_root)
    lock_copy = workspace / expected_lock["filename"]
    wheel = workspace / "wheelhouse" / expected_package["wheel_filename"]
    freeze = workspace / "requirements.freeze.txt"
    if (
        not wheel.is_file()
        or sha256_file(wheel) != expected_package["wheel_sha256"]
        or not lock_copy.is_file()
        or sha256_file(lock_copy) != expected_lock["sha256"]
        or lock_copy.read_bytes() != lock_authority.read_bytes()
    ):
        raise RunnerEnvironmentError("legacy pinned artifact authority mismatch")
    if (
        not freeze.is_file()
        or sha256_file(freeze) != manifest.get("freeze_sha256")
        or not isinstance(manifest.get("python"), dict)
        or set(manifest["python"]) != {
            "executable_name",
            "executable_sha256",
            "version",
        }
        or not isinstance(manifest["python"].get("executable_sha256"), str)
        or len(manifest["python"]["executable_sha256"]) != 64
    ):
        raise RunnerEnvironmentError("legacy runner provenance is incomplete")


def _construct_runner_environment(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    build_root: Path,
    *,
    final_environment_root: Path,
    controlled_migration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a candidate solely from current pinned public artifact authority."""
    lock_authority = _dependency_lock(config, repository)
    environment_root = build_root / ".venv"
    wheelhouse = build_root / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / config.data["engine_kit"]["release"]["wheel_filename"]
    lock_copy = build_root / "requirements-production.lock"
    write_bytes_atomic(wheel, _download_release_wheel(config))
    write_bytes_atomic(lock_copy, lock_authority.read_bytes())
    venv.EnvBuilder(with_pip=True, symlinks=True).create(environment_root)
    python = environment_root / "bin" / "python"
    _run([str(python), "-m", "pip", "install", "--require-hashes", "-r", str(lock_copy)])
    _run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)])
    _run([str(python), "-m", "pip", "check"])
    freeze = _freeze(python)
    write_bytes_atomic(build_root / "requirements.freeze.txt", freeze)
    observed = _probe_subprocess(python)
    _validate_import_location(observed, environment_root)
    version = _run([str(python), "--version"])
    kit = config.data["engine_kit"]
    manifest = {
        "schema_version": ENVIRONMENT_SCHEMA,
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "engine_kit_source_commit": kit["source_commit"],
        "engine_kit_release_commit": kit["release_commit"],
        "directory_durability": _directory_durability_identity(config, runtime_root),
        "engine_kit_package": {
            "distribution_name": observed["distribution_name"],
            "distribution_version": observed["distribution_version"],
            "wheel_filename": wheel.name,
            "wheel_sha256": sha256_file(wheel),
            "wheel_source_url": _release_wheel_url(config),
            "record_sha256": observed["record_sha256"],
            "installation_mode": "public-release-wheel-plus-hash-lock",
        },
        "dependency_lock": {
            "filename": lock_copy.name,
            "sha256": sha256_file(lock_copy),
            "install_mode": "pip-install-require-hashes",
        },
        "python": {
            "executable_name": Path(observed["executable"]).name,
            "executable_sha256": sha256_file(python),
            "version": version.stdout.strip() or version.stderr.strip(),
        },
        "freeze_file": "requirements.freeze.txt",
        "freeze_sha256": hashlib.sha256(freeze).hexdigest(),
        "environment_content_sha256": _environment_content_sha256(environment_root),
        "environment_path_identity": path_identity(
            final_environment_root, "campaign-runner-venv"
        ),
    }
    if controlled_migration is not None:
        manifest["controlled_migration"] = controlled_migration
    return manifest


def _migrate_legacy_runner_environment(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    workspace: Path,
    legacy_manifest_sha256: str,
) -> dict[str, Any]:
    migration = {
        "protocol": LEGACY_MIGRATION_PROTOCOL,
        "from_public_runner_authority": LEGACY_PUBLIC_RUNNER_AUTHORITY,
        "legacy_environment_manifest_sha256": legacy_manifest_sha256,
    }
    staging = Path(tempfile.mkdtemp(prefix=".environment-migration-", dir=workspace))
    fsync_directory(staging)
    fsync_directory(workspace)
    try:
        candidate = _construct_runner_environment(
            config,
            repository,
            runtime_root,
            staging,
            final_environment_root=workspace / ".venv",
            controlled_migration=migration,
        )
        # The old installation is evidence only. It never contributes bytes or identity
        # to the candidate reconstructed from the pinned wheel and committed hash lock.
        suffix = legacy_manifest_sha256[:12]
        for name in (".venv", "wheelhouse"):
            current = workspace / name
            if current.exists():
                backup = workspace / f".legacy-{name.lstrip('.')}-{suffix}"
                if backup.exists():
                    raise RunnerEnvironmentError("legacy migration evidence path already exists")
                os.replace(current, backup)
                fsync_directory(workspace)
            os.replace(staging / name, current)
            fsync_directory(workspace)
        for name in ("requirements-production.lock", "requirements.freeze.txt"):
            os.replace(staging / name, workspace / name)
            fsync_directory(workspace)
        if _environment_content_sha256(workspace / ".venv") != candidate["environment_content_sha256"]:
            raise RunnerEnvironmentError("rebuilt runner environment content verification failed")
        write_json(workspace / "environment_manifest.json", candidate)
        verified = verify_runner_environment(
            config, repository, runtime_root, require_active=False
        )
        staging.rmdir()
        fsync_directory(workspace)
        return verified
    except Exception as exc:
        if isinstance(exc, RunnerEnvironmentError):
            raise
        raise RunnerEnvironmentError(
            f"legacy runner environment rebuild failed; preserved migration evidence: {staging}"
        ) from exc


def verify_runner_environment(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    *,
    require_active: bool,
    allow_legacy_missing_durability: bool = False,
) -> dict[str, Any]:
    workspace = runner_workspace(config, runtime_root)
    environment_root = workspace / ".venv"
    manifest_path = workspace / "environment_manifest.json"
    freeze_path = workspace / "requirements.freeze.txt"
    wheelhouse = workspace / "wheelhouse"
    lock_copy = workspace / "requirements-production.lock"
    if not manifest_path.is_file() or not freeze_path.is_file() or not environment_root.is_dir():
        raise RunnerEnvironmentError("campaign runner environment is absent or incomplete; run bootstrap")
    manifest, _ = _read_environment_manifest(workspace)
    kit = config.data["engine_kit"]
    expected = {
        "schema_version": ENVIRONMENT_SCHEMA,
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "engine_kit_source_commit": kit["source_commit"],
        "engine_kit_release_commit": kit["release_commit"],
        "directory_durability": _directory_durability_identity(config, runtime_root),
    }
    if allow_legacy_missing_durability and "directory_durability" not in manifest:
        expected.pop("directory_durability")
    conflicts = [key for key, value in expected.items() if manifest.get(key) != value]
    if conflicts:
        raise RunnerEnvironmentError("runner environment authority mismatch: " + ", ".join(conflicts))
    lock_authority = _dependency_lock(config, repository)
    package = manifest.get("engine_kit_package", {})
    if package.get("installation_mode") != "public-release-wheel-plus-hash-lock":
        raise RunnerEnvironmentError("Engine Kit installation mode is not public-release-wheel-plus-hash-lock")
    if (
        package.get("wheel_filename") != kit["release"]["wheel_filename"]
        or package.get("wheel_sha256") != kit["release"]["wheel_sha256"]
    ):
        raise RunnerEnvironmentError("Engine Kit package wheel authority mismatch")
    wheel = wheelhouse / str(package.get("wheel_filename", ""))
    if not wheel.is_file() or sha256_file(wheel) != kit["release"]["wheel_sha256"]:
        raise RunnerEnvironmentError("Engine Kit runner wheel identity mismatch")
    if package.get("wheel_source_url") != _release_wheel_url(config):
        raise RunnerEnvironmentError("Engine Kit wheel source URL differs from release authority")
    if not lock_copy.is_file() or sha256_file(lock_copy) != kit["production_dependency_lock"]["sha256"]:
        raise RunnerEnvironmentError("runner dependency lock identity mismatch")
    if lock_copy.read_bytes() != lock_authority.read_bytes():
        raise RunnerEnvironmentError("runner dependency lock differs from committed authority")
    python = environment_root / "bin" / "python"
    if not python.is_file():
        raise RunnerEnvironmentError("campaign runner Python is absent")
    if require_active:
        observed = _distribution_identity()
        observed.update({"prefix": sys.prefix, "executable": sys.executable})
    else:
        observed = _probe_subprocess(python)
    _validate_import_location(observed, environment_root)
    if require_active and Path(sys.executable).resolve() != python.resolve():
        raise RunnerEnvironmentError("active Python executable is not the campaign runner Python")
    if observed.get("distribution_name") != package.get("distribution_name"):
        raise RunnerEnvironmentError("Engine Kit installed distribution identity mismatch")
    if observed.get("distribution_version") != "0.4.0" or observed.get("distribution_version") != package.get("distribution_version"):
        raise RunnerEnvironmentError("Engine Kit installed version identity mismatch")
    if observed.get("record_sha256") != package.get("record_sha256"):
        raise RunnerEnvironmentError("Engine Kit installed RECORD identity mismatch")
    direct_url = observed.get("direct_url") or {}
    archive_info = direct_url.get("archive_info", {}) if isinstance(direct_url, dict) else {}
    hashes = archive_info.get("hashes", {}) if isinstance(archive_info, dict) else {}
    installed_sha = hashes.get("sha256") if isinstance(hashes, dict) else None
    if installed_sha is not None and installed_sha != kit["release"]["wheel_sha256"]:
        raise RunnerEnvironmentError("installed Engine Kit wheel origin identity mismatch")
    archive_hash = archive_info.get("hash") if isinstance(archive_info, dict) else None
    if archive_hash is not None and archive_hash != f"sha256={kit['release']['wheel_sha256']}":
        raise RunnerEnvironmentError("installed Engine Kit wheel archive hash mismatch")
    freeze = _freeze(python)
    if freeze_path.read_bytes() != freeze or hashlib.sha256(freeze).hexdigest() != manifest.get("freeze_sha256"):
        raise RunnerEnvironmentError("runner dependency freeze identity mismatch")
    if sha256_file(python) != manifest.get("python", {}).get("executable_sha256"):
        raise RunnerEnvironmentError("runner Python executable identity mismatch")
    if _environment_content_sha256(environment_root) != manifest.get("environment_content_sha256"):
        raise RunnerEnvironmentError("runner environment content inventory mismatch")
    identity = dict(manifest)
    identity["environment_manifest_sha256"] = sha256_file(manifest_path)
    return identity


def bootstrap_runner_environment(config: CampaignConfig, repository: Path, runtime_root: Path) -> dict[str, Any]:
    """Create once or strictly reconcile the public release-backed runner environment."""
    workspace_existed = runner_workspace(config, runtime_root).exists()
    workspace = durably_establish_runner_workspace(config, runtime_root)
    if workspace_existed:
        if not (workspace / "environment_manifest.json").is_file():
            verify_runner_environment(
                config, repository, runtime_root, require_active=False
            )
        existing, existing_sha256 = _read_environment_manifest(workspace)
        if "environment_content_sha256" not in existing:
            _validate_legacy_migration_authority(
                config, repository, runtime_root, existing
            )
            manifest = _migrate_legacy_runner_environment(
                config, repository, runtime_root, workspace, existing_sha256
            )
            return {"status": "migrated", "runner_environment": manifest}
        manifest = verify_runner_environment(
            config,
            repository,
            runtime_root,
            require_active=False,
            allow_legacy_missing_durability=True,
        )
        if "directory_durability" not in manifest:
            manifest_path = workspace / "environment_manifest.json"
            upgraded = json.loads(manifest_path.read_text(encoding="utf-8"))
            upgraded["directory_durability"] = _directory_durability_identity(
                config, runtime_root
            )
            write_json(manifest_path, upgraded)
            manifest = verify_runner_environment(
                config, repository, runtime_root, require_active=False
            )
        return {"status": "reconciled", "runner_environment": manifest}

    try:
        manifest = _construct_runner_environment(
            config,
            repository,
            runtime_root,
            workspace,
            final_environment_root=workspace / ".venv",
        )
        write_json(workspace / "environment_manifest.json", manifest)
        verified = verify_runner_environment(config, repository, runtime_root, require_active=False)
        return {"status": "created", "runner_environment": verified}
    except Exception as exc:
        raise RunnerEnvironmentError(
            f"runner environment bootstrap failed; preserved conflicting workspace for review: {workspace}"
        ) from exc
