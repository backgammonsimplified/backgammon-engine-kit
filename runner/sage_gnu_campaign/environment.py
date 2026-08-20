"""Campaign-owned immutable Engine Kit runner environment."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import subprocess
import sys
import tarfile
import venv
from pathlib import Path
from typing import Any

from .config import CampaignConfig
from .manifests import path_identity, sha256_file, write_bytes_atomic, write_json


ENVIRONMENT_SCHEMA = "sage-gnu-runner-environment-v1"


class RunnerEnvironmentError(RuntimeError):
    """The campaign runner environment is missing, mutable, or conflicts with authority."""


def runner_workspace(config: CampaignConfig, runtime_root: Path) -> Path:
    return Path(runtime_root).resolve() / config.campaign_id / "runner-workspace"


def runner_venv(config: CampaignConfig, runtime_root: Path) -> Path:
    return runner_workspace(config, runtime_root) / ".venv"


def pair_attempt_workspace(
    config: CampaignConfig,
    runtime_root: Path,
    pair_id: str,
    attempt: int,
) -> Path:
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
    output = _run([str(python), "-m", "pip", "freeze", "--all"]).stdout
    return output.encode("utf-8")


def _git_archive(repository: Path, commit: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(Path(repository).resolve()), "archive", "--format=tar", commit],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "git archive failed"
        raise RunnerEnvironmentError(detail)
    return result.stdout


def _extract_source_archive(archive: Path, destination: Path) -> None:
    destination.mkdir()
    with tarfile.open(archive, mode="r:") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if (
                not target.is_relative_to(destination.resolve())
                or member.issym()
                or member.islnk()
            ):
                raise RunnerEnvironmentError("Engine Kit source archive contains an unsafe member")
        bundle.extractall(destination)


def _distribution_identity() -> dict[str, Any]:
    package = importlib.import_module("backgammon_engine_kit")
    distribution = importlib.metadata.distribution("backgammon-engine-kit")
    dist_info = Path(distribution._path).resolve()  # type: ignore[attr-defined]
    record = dist_info / "RECORD"
    if not record.is_file():
        raise RunnerEnvironmentError("installed Engine Kit distribution lacks RECORD identity")
    direct_url_path = dist_info / "direct_url.json"
    direct_url = (
        json.loads(direct_url_path.read_text(encoding="utf-8"))
        if direct_url_path.is_file()
        else None
    )
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
}
value.update({"prefix": sys.prefix, "executable": sys.executable})
print(json.dumps(value, sort_keys=True))
"""
    result = _run([str(python), "-c", script])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerEnvironmentError("runner environment identity probe was malformed") from exc


def _validate_import_location(
    observed: dict[str, Any],
    environment_root: Path,
    engine_kit_root: Path,
) -> None:
    environment_root = Path(environment_root).resolve()
    engine_kit_root = Path(engine_kit_root).resolve()
    prefix = Path(observed["prefix"]).resolve()
    module_file = Path(observed["module_file"]).resolve()
    dist_info = Path(observed["dist_info"]).resolve()
    if prefix != environment_root:
        raise RunnerEnvironmentError("active Python is not the campaign-owned runner environment")
    if not module_file.is_relative_to(environment_root) or not dist_info.is_relative_to(environment_root):
        raise RunnerEnvironmentError("imported Engine Kit does not resolve under the runner environment")
    if module_file.is_relative_to(engine_kit_root):
        raise RunnerEnvironmentError("Engine Kit source-checkout import leakage is forbidden")
    direct_url = observed.get("direct_url")
    if direct_url:
        if not isinstance(direct_url, dict):
            raise RunnerEnvironmentError("Engine Kit direct installation identity is malformed")
        if direct_url.get("dir_info", {}).get("editable") is True:
            raise RunnerEnvironmentError("editable Engine Kit installation is forbidden")
        url = str(direct_url.get("url", ""))
        if str(engine_kit_root) in url:
            raise RunnerEnvironmentError("Engine Kit installation points at the source checkout")


def verify_runner_environment(
    config: CampaignConfig,
    runtime_root: Path,
    engine_kit_root: Path,
    *,
    require_active: bool,
) -> dict[str, Any]:
    workspace = runner_workspace(config, runtime_root)
    environment_root = workspace / ".venv"
    manifest_path = workspace / "environment_manifest.json"
    freeze_path = workspace / "requirements.freeze.txt"
    wheelhouse = workspace / "wheelhouse"
    if not manifest_path.is_file() or not freeze_path.is_file() or not environment_root.is_dir():
        raise RunnerEnvironmentError("campaign runner environment is absent or incomplete; run bootstrap")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerEnvironmentError("campaign runner environment manifest is unreadable") from exc
    expected = {
        "schema_version": ENVIRONMENT_SCHEMA,
        "campaign_id": config.campaign_id,
        "campaign_configuration_sha256": config.content_sha256,
        "engine_kit_source_commit": config.data["engine_kit"]["source_commit"],
    }
    conflicts = [key for key, value in expected.items() if manifest.get(key) != value]
    if conflicts:
        raise RunnerEnvironmentError("runner environment authority mismatch: " + ", ".join(conflicts))
    python = environment_root / "bin" / "python"
    if not python.is_file():
        raise RunnerEnvironmentError("campaign runner Python is absent")
    if require_active:
        observed = _distribution_identity()
        observed.update({"prefix": sys.prefix, "executable": sys.executable})
    else:
        observed = _probe_subprocess(python)
    _validate_import_location(observed, environment_root, engine_kit_root)
    if require_active and Path(sys.executable).resolve() != python.resolve():
        raise RunnerEnvironmentError("active Python executable is not the campaign runner Python")
    package = manifest.get("engine_kit_package", {})
    if package.get("installation_mode") != "non-editable-wheel":
        raise RunnerEnvironmentError("Engine Kit installation mode is not the commissioned wheel mode")
    source_archive = workspace / str(package.get("source_archive_filename", ""))
    if (
        not source_archive.is_file()
        or sha256_file(source_archive) != package.get("source_archive_sha256")
    ):
        raise RunnerEnvironmentError("Engine Kit source archive identity mismatch")
    wheel = wheelhouse / str(package.get("wheel_filename", ""))
    if not wheel.is_file() or sha256_file(wheel) != package.get("wheel_sha256"):
        raise RunnerEnvironmentError("Engine Kit wheel artifact identity mismatch")
    if observed.get("distribution_name") != package.get("distribution_name"):
        raise RunnerEnvironmentError("Engine Kit installed distribution identity mismatch")
    if observed.get("distribution_version") != package.get("distribution_version"):
        raise RunnerEnvironmentError("Engine Kit installed version identity mismatch")
    if observed.get("record_sha256") != package.get("record_sha256"):
        raise RunnerEnvironmentError("Engine Kit installed RECORD identity mismatch")
    direct_url = observed.get("direct_url") or {}
    installed_wheel_sha256 = direct_url.get("archive_info", {}).get("hashes", {}).get("sha256")
    if installed_wheel_sha256 is not None and installed_wheel_sha256 != package.get("wheel_sha256"):
        raise RunnerEnvironmentError("installed Engine Kit wheel origin identity mismatch")
    freeze = _freeze(python)
    freeze_sha256 = hashlib.sha256(freeze).hexdigest()
    if freeze_path.read_bytes() != freeze or freeze_sha256 != manifest.get("freeze_sha256"):
        raise RunnerEnvironmentError("runner dependency freeze identity mismatch")
    if sha256_file(python) != manifest.get("python", {}).get("executable_sha256"):
        raise RunnerEnvironmentError("runner Python executable identity mismatch")
    identity = dict(manifest)
    identity["environment_manifest_sha256"] = sha256_file(manifest_path)
    return identity


def bootstrap_runner_environment(
    config: CampaignConfig,
    runtime_root: Path,
    engine_kit_root: Path,
    engine_kit_identity: dict[str, Any],
) -> dict[str, Any]:
    """Create once or strictly reconcile the campaign-owned non-editable environment."""
    workspace = runner_workspace(config, runtime_root)
    if workspace.exists():
        manifest = verify_runner_environment(
            config,
            runtime_root,
            engine_kit_root,
            require_active=False,
        )
        return {"status": "reconciled", "runner_environment": manifest}

    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir()
    environment_root = workspace / ".venv"
    wheelhouse = workspace / "wheelhouse"
    source_archive = workspace / "engine-kit-source.tar"
    source_tree = workspace / "engine-kit-source"
    wheelhouse.mkdir()
    try:
        source_commit = engine_kit_identity["source_commit"]
        write_bytes_atomic(source_archive, _git_archive(engine_kit_root, source_commit))
        _extract_source_archive(source_archive, source_tree)
        venv.EnvBuilder(with_pip=True, symlinks=True).create(environment_root)
        python = environment_root / "bin" / "python"
        _run(
            [
                str(python),
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                str(wheelhouse),
                str(source_tree),
            ]
        )
        wheels = sorted(wheelhouse.glob("backgammon_engine_kit-*.whl"))
        if len(wheels) != 1:
            raise RunnerEnvironmentError("Engine Kit build did not produce exactly one immutable wheel")
        wheel = wheels[0]
        _run([str(python), "-m", "pip", "install", str(wheel)])
        freeze = _freeze(python)
        write_bytes_atomic(workspace / "requirements.freeze.txt", freeze)
        observed = _probe_subprocess(python)
        _validate_import_location(observed, environment_root, engine_kit_root)
        version = _run([str(python), "--version"])
        manifest = {
            "schema_version": ENVIRONMENT_SCHEMA,
            "campaign_id": config.campaign_id,
            "campaign_configuration_sha256": config.content_sha256,
            "engine_kit_source_commit": engine_kit_identity["source_commit"],
            "engine_kit_package": {
                "distribution_name": observed["distribution_name"],
                "distribution_version": observed["distribution_version"],
                "wheel_filename": wheel.name,
                "wheel_sha256": sha256_file(wheel),
                "record_sha256": observed["record_sha256"],
                "installation_mode": "non-editable-wheel",
                "source_archive_filename": source_archive.name,
                "source_archive_sha256": sha256_file(source_archive),
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
        verified = verify_runner_environment(
            config,
            runtime_root,
            engine_kit_root,
            require_active=False,
        )
        return {"status": "created", "runner_environment": verified}
    except Exception as exc:
        raise RunnerEnvironmentError(
            f"runner environment bootstrap failed; preserved conflicting workspace for review: {workspace}"
        ) from exc
