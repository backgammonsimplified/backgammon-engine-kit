"""Campaign bootstrap plus read-only public release, environment, and root safety checks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import CampaignConfig
from .engine_kit import EngineKitSession
from .environment import bootstrap_runner_environment, verify_runner_environment
from .manifests import benchmarker_git_identity, engine_kit_release_identity, path_identity


class PreflightError(RuntimeError):
    """The operator environment is not authorized-ready for this campaign."""


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def validate_roots(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    artifact_root: Path,
) -> dict[str, dict[str, str]]:
    repository = Path(repository).resolve()
    runtime_root = Path(runtime_root).resolve()
    artifact_root = Path(artifact_root).resolve()
    if _overlaps(runtime_root, artifact_root):
        raise PreflightError("runtime and durable artifact roots must be separate")
    for candidate, label in ((runtime_root, "runtime"), (artifact_root, "artifact")):
        if _overlaps(candidate, repository):
            raise PreflightError(f"{label} root must be outside the public campaign checkout")
        lowered = {part.lower() for part in candidate.parts}
        forbidden = {part.lower() for part in config.data["runtime_policy"]["forbidden_path_components"]}
        if lowered & forbidden:
            raise PreflightError(f"{label} root matches a forbidden cross-campaign path component")
    return {
        "runtime_root": path_identity(runtime_root, "runner-runtime-root"),
        "artifact_root": path_identity(artifact_root, "durable-artifact-root"),
    }


def bootstrap(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    roots = validate_roots(config, repository, runtime_root, artifact_root)
    benchmarker = benchmarker_git_identity(repository, config)
    engine_kit = engine_kit_release_identity(repository, config)
    environment = bootstrap_runner_environment(config, repository, runtime_root)
    return {
        "status": environment["status"],
        "benchmarker": benchmarker,
        "engine_kit": engine_kit,
        "runner_environment": environment["runner_environment"],
        "roots": roots,
    }


def preflight(
    config: CampaignConfig,
    repository: Path,
    runtime_root: Path,
    artifact_root: Path,
    *,
    require_clean_benchmarker: bool,
    load_engine_runtime: bool = True,
) -> dict[str, Any]:
    repository = Path(repository).resolve()
    expected_config = repository / "experiments/sage-gnu-campaign-v1/campaign.json"
    if config.path != expected_config:
        raise PreflightError("execution requires the repository-owned committed campaign configuration path")
    roots = validate_roots(config, repository, runtime_root, artifact_root)
    benchmarker = benchmarker_git_identity(repository, config)
    if require_clean_benchmarker and not benchmarker["clean"]:
        raise PreflightError("real campaign execution requires a clean public campaign checkout")
    engine_kit = engine_kit_release_identity(repository, config)
    environment = verify_runner_environment(config, repository, runtime_root, require_active=True)
    runtime = EngineKitSession(config).public_identity() if load_engine_runtime else None
    return {
        "status": "pass",
        "benchmarker": benchmarker,
        "engine_kit": engine_kit,
        "engine_runtime": runtime,
        "runner_environment": environment,
        "roots": roots,
    }
