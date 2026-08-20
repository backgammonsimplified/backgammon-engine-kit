"""Campaign-owned runner environment built only from committed public release assets."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path
from typing import Any

from .config import CampaignConfig
from .manifests import path_identity, sha256_file, write_bytes_atomic, write_json


ENVIRONMENT_SCHEMA = "sage-gnu-runner-environment-v2"


class RunnerEnvironmentError(RuntimeError):
    """The campaign runner environment is missing, mutable, or conflicts with authority."""


def runner_workspace(config: CampaignConfig, runtime_root: Path) -> Path:
    return Path(runtime_root).resolve() / config.campaign_id / "runner-workspace"


def runner_venv(config: CampaignConfig, runtime_root: Path) -> Path:
    return runner_workspace(config, runtime_root) / ".venv"


def pair_attempt_workspace(config: CampaignConfig, runtime_root: Path, pair_id: str, attempt: int) -> Path:
    return runner_workspace(config, runtime_root) / pair_id / f"attempt-{attempt}"


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


def verify_runner_environment(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    *,
    require_active: bool,
) -> dict[str, Any]:
    workspace = runner_workspace(config, runtime_root)
    environment_root = workspace / ".venv"
    manifest_path = workspace / "environment_manifest.json"
    freeze_path = workspace / "requirements.freeze.txt"
    wheelhouse = workspace / "wheelhouse"
    lock_copy = workspace / "requirements-production.lock"
    if not manifest_path.is_file() or not freeze_path.is_file() or not environment_root.is_dir():
        raise RunnerEnvironmentError("campaign runner environment is absent or incomplete; run bootstrap")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerEnvironmentError("campaign runner environment manifest is unreadable") from exc
    kit = config.data["engine_kit"]
    expected = {
        "schema_version": ENVIRONMENT_SCHEMA,
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "engine_kit_source_commit": kit["source_commit"],
        "engine_kit_release_commit": kit["release_commit"],
    }
    conflicts = [key for key, value in expected.items() if manifest.get(key) != value]
    if conflicts:
        raise RunnerEnvironmentError("runner environment authority mismatch: " + ", ".join(conflicts))
    lock_authority = _dependency_lock(config, repository)
    package = manifest.get("engine_kit_package", {})
    if package.get("installation_mode") != "public-release-wheel-plus-hash-lock":
        raise RunnerEnvironmentError("Engine Kit installation mode is not public-release-wheel-plus-hash-lock")
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
    identity = dict(manifest)
    identity["environment_manifest_sha256"] = sha256_file(manifest_path)
    return identity


def bootstrap_runner_environment(config: CampaignConfig, repository: Path, runtime_root: Path) -> dict[str, Any]:
    """Create once or strictly reconcile the public release-backed runner environment."""
    workspace = runner_workspace(config, runtime_root)
    if workspace.exists():
        manifest = verify_runner_environment(config, repository, runtime_root, require_active=False)
        return {"status": "reconciled", "runner_environment": manifest}

    lock_authority = _dependency_lock(config, repository)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir()
    environment_root = workspace / ".venv"
    wheelhouse = workspace / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / config.data["engine_kit"]["release"]["wheel_filename"]
    lock_copy = workspace / "requirements-production.lock"
    try:
        write_bytes_atomic(wheel, _download_release_wheel(config))
        write_bytes_atomic(lock_copy, lock_authority.read_bytes())
        venv.EnvBuilder(with_pip=True, symlinks=True).create(environment_root)
        python = environment_root / "bin" / "python"
        _run([str(python), "-m", "pip", "install", "--require-hashes", "-r", str(lock_copy)])
        _run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)])
        _run([str(python), "-m", "pip", "check"])
        freeze = _freeze(python)
        write_bytes_atomic(workspace / "requirements.freeze.txt", freeze)
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
            "environment_path_identity": path_identity(environment_root, "campaign-runner-venv"),
        }
        write_json(workspace / "environment_manifest.json", manifest)
        verified = verify_runner_environment(config, repository, runtime_root, require_active=False)
        return {"status": "created", "runner_environment": verified}
    except Exception as exc:
        raise RunnerEnvironmentError(
            f"runner environment bootstrap failed; preserved conflicting workspace for review: {workspace}"
        ) from exc
