"""Public-safe provenance manifests and deterministic checksums."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .config import CampaignConfig


MANIFEST_SCHEMA = "sage-gnu-campaign-manifest-v1"


class ProvenanceError(RuntimeError):
    """Required launch or immutable-output provenance does not reconcile."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    """Durably replace one file through a same-directory temporary."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.tmp-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_bytes_atomic(path, payload)


def fsync_tree(root: Path) -> None:
    """Flush every regular file and directory below a publication root."""
    root = Path(root)
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ProvenanceError(f"publication tree contains a symbolic link: {path}")
        if path.is_dir():
            directories.append(path)
        elif path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)


def git_output(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode != 0:
        raise ProvenanceError(result.stderr.strip() or "git provenance command failed")
    return result.stdout.strip()


def benchmarker_git_identity(repository: Path, config: CampaignConfig) -> dict[str, Any]:
    repository = Path(repository).resolve()
    head = git_output(repository, "rev-parse", "HEAD")
    branch = git_output(repository, "branch", "--show-current")
    status = git_output(repository, "status", "--porcelain", "--untracked-files=all")
    expected = config.data["benchmarker"]
    if branch != expected["branch"]:
        raise ProvenanceError(f"Benchmarker branch mismatch: {branch}")
    return {
        "repository": expected["repository"],
        "branch": branch,
        "commit": head,
        "clean": not bool(status),
        "dirty_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest() if status else None,
    }


def engine_kit_release_identity(repository: Path, config: CampaignConfig) -> dict[str, Any]:
    repository = Path(repository).resolve()
    expected = config.data["engine_kit"]
    lock = repository / expected["production_dependency_lock"]["path"]
    if not lock.is_file() or sha256_file(lock) != expected["production_dependency_lock"]["sha256"]:
        raise ProvenanceError("committed Engine Kit production dependency lock identity mismatch")
    release = expected["release"]
    wheel_url = (
        f"https://raw.githubusercontent.com/{expected['repository']}/{release['tag']}"
        f"/release-assets/v0.4.0/{release['wheel_filename']}"
    )
    return {
        "repository": expected["repository"],
        "branch": expected["branch"],
        "source_commit": expected["source_commit"],
        "release_commit": expected["release_commit"],
        "base_commit": expected["base_commit"],
        "release": expected["release"],
        "production_dependency_lock": expected["production_dependency_lock"],
        "wheel_source_url": wheel_url,
        "dependency_lock_asset": {
            "path": lock.relative_to(repository).as_posix(),
            "sha256": sha256_file(lock),
        },
        "installation_authority": "public-release-wheel-plus-committed-hash-lock",
    }


def path_identity(path: Path, label: str) -> dict[str, str]:
    resolved = str(Path(path).resolve())
    return {
        "label": label,
        "basename": Path(resolved).name,
        "resolved_path_sha256": hashlib.sha256(resolved.encode("utf-8")).hexdigest(),
    }


def host_identity() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable_sha256": sha256_file(Path(sys.executable)),
        "cpu_count": os.cpu_count(),
    }


def sanitized_launch_command(argv: Iterable[str], path_values: Iterable[str] = ()) -> str:
    replacements = {str(Path(value).resolve()): f"<{index}_ROOT>" for index, value in enumerate(path_values, 1)}
    sanitized = []
    for argument in argv:
        rendered = str(argument)
        for private, token in replacements.items():
            rendered = rendered.replace(private, token)
        sanitized.append(rendered)
    return shlex.join(sanitized)


def checksum_entries(root: Path, excluded: Iterable[str] = ()) -> dict[str, str]:
    root = Path(root)
    excluded_set = set(excluded)
    entries = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded_set or path.name.endswith((".tmp", ".lock")):
            continue
        entries[relative] = sha256_file(path)
    return entries


def checksum_text(entries: dict[str, str]) -> bytes:
    return ("".join(f"{digest}  {relative}\n" for relative, digest in sorted(entries.items()))).encode("utf-8")


def verify_checksum_file(root: Path, checksum_path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64 or relative in entries:
            raise ProvenanceError("malformed checksum manifest")
        entries[relative] = digest
    for relative, expected in entries.items():
        path = Path(root) / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ProvenanceError(f"immutable output checksum mismatch: {relative}")
    return entries


def common_manifest(
    config: CampaignConfig,
    benchmarker: dict[str, Any],
    engine_kit: dict[str, Any],
    engine_runtime: dict[str, Any],
    runner_environment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "campaign_id": config.campaign_id,
        "campaign_configuration": {
            "path": config.path.relative_to(config.path.parents[2]).as_posix(),
            "sha256": config.content_sha256,
            "schema_version": config.schema_version,
        },
        "benchmarker": benchmarker,
        "engine_kit": engine_kit,
        "engine_runtime": engine_runtime,
        "runner_environment": runner_environment,
        "configured_profile": {
            "sage_checker_target": config.data["engines"]["sage"]["checker_configured_target"],
            "sage_cube_target": config.data["engines"]["sage"]["cube_configured_target"],
            "gnu_checker_target": config.data["engines"]["gnu"]["checker_configured_target"],
            "gnu_cube_target": config.data["engines"]["gnu"]["cube_configured_target"],
            "gnu_move_filter_identity": config.data["engines"]["gnu"]["checker_move_filter_identity"],
            "sage_threads": config.data["engines"]["sage"]["threads"],
            "gnu_threads": config.data["engines"]["gnu"]["threads"],
        },
        "match_length_points": config.data["match"]["length_points"],
        "mirrored_physical_seat_mapping": config.data["match"]["members"],
        "dice_protocol": config.data["dice"]["protocol"],
        "candidate_actual_depth_evidence_template": config.data["manifest_policy"]["candidate_actual_depth_evidence"],
    }
