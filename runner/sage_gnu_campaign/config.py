"""Load and fail-closed validate the committed campaign authority."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_SCHEMA = "sage-gnu-campaign-config-v1"
DEFAULT_CONFIG = Path("experiments/sage-gnu-campaign-v1/campaign.json")
GNU_NORMAL_V1 = (
    "normal-v1;1:0=0,8,0.160;2:0=0,8,0.160|1=skip;"
    "3:0=0,8,0.160|1=skip|2=0,2,0.040;"
    "4:0=0,8,0.160|1=skip|2=0,2,0.040|3=skip"
)


class ConfigurationError(ValueError):
    """The committed campaign configuration is absent, changed, or unsafe."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class CampaignConfig:
    path: Path
    data: dict[str, Any]
    content_sha256: str

    @property
    def schema_version(self) -> str:
        return str(self.data["schema_version"])

    @property
    def campaign_id(self) -> str:
        return str(self.data["campaign_id"])

    @property
    def pair_count(self) -> int:
        return int(self.data["bounds"]["pair_count"])

    def semantics_identity(self) -> dict[str, Any]:
        """Return the immutable experiment authority used for pair identities."""
        return {
            "campaign_id": self.campaign_id,
            "schema_version": self.schema_version,
            "pair_bound": self.data["bounds"]["pair_count"],
            "match": self.data["match"],
            "dice": self.data["dice"],
            "engines": {
                name: {key: value for key, value in engine.items() if key != "runtime_identity"}
                for name, engine in self.data["engines"].items()
            },
            "engine_kit_source": {
                "repository": self.data["engine_kit"]["repository"],
                "source_commit": self.data["engine_kit"]["source_commit"],
            },
            "identity": self.data["identity"],
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigurationError(message)


def _validate(data: dict[str, Any]) -> None:
    _require(data.get("schema_version") == CONFIG_SCHEMA, "unsupported campaign schema")
    _require(data.get("campaign_id") == "sage4-gnu3-7pt-mirrored-v1", "campaign id changed")
    _require(data.get("match", {}).get("length_points") == 7, "match length must be seven points")
    members = data.get("match", {}).get("members", {})
    _require(
        members == {
            "A": {"gnu_physical_seat": "X", "sage_physical_seat": "O"},
            "B": {"gnu_physical_seat": "O", "sage_physical_seat": "X"},
        },
        "mirrored physical-seat mapping changed",
    )
    engines = data.get("engines", {})
    sage = engines.get("sage", {})
    gnu = engines.get("gnu", {})
    _require(sage.get("checker_configured_target") == "4ply", "Sage checker target must be 4ply")
    _require(sage.get("cube_configured_target") == "3ply", "Sage cube target must be 3ply")
    _require(gnu.get("checker_configured_target") == "3ply", "GNU checker target must be 3ply")
    _require(gnu.get("cube_configured_target") == "2ply", "GNU cube target must be 2ply")
    _require(gnu.get("checker_move_filter_identity") == GNU_NORMAL_V1, "GNU filter must be normal-v1")
    _require(sage.get("threads") == 1, "Sage commissioning threads must remain one")
    _require(gnu.get("threads") == 1, "GNU commissioning threads must remain one")
    kit = data.get("engine_kit", {})
    _require(
        kit.get("source_commit") == "f87c69b10efa707f52aa1e42c74808d9b3bc109f",
        "Engine Kit package source commit changed",
    )
    _require(
        kit.get("release_commit") == "f13446140ea06f9dc1ef51d4b6b0c83c5a46237d",
        "Engine Kit release commit changed",
    )
    _require(kit.get("branch") == "release/v0.4.0", "Engine Kit release branch changed")
    _require(kit.get("release", {}).get("tag") == "v0.4.0", "Engine Kit release tag changed")
    _require(bool(kit.get("release", {}).get("wheel_sha256")), "Engine Kit wheel identity missing")
    _require(bool(kit.get("release", {}).get("sdist_sha256")), "Engine Kit sdist identity missing")
    _require(bool(kit.get("production_dependency_lock", {}).get("sha256")), "Engine Kit dependency lock identity missing")
    _require(isinstance(data.get("bounds", {}).get("pair_count"), int), "pair bound must be an integer")
    _require(data["bounds"]["pair_count"] > 0, "pair bound must be positive")
    dice = data.get("dice", {})
    _require(dice.get("protocol") == "physical-seat-dice-stream-v1", "dice protocol changed")
    _require(
        dice.get("namespace_seed_format") == "{base_seed}:match:{side}",
        "historical A/B namespace seed format changed",
    )
    _require(data.get("resume_policy", {}).get("committed_pair", "").startswith("verify-and-skip"), "unsafe committed-pair policy")
    release = kit.get("release", {})
    _require(
        set(release) == {"tag", "wheel_filename", "wheel_sha256", "sdist_filename", "sdist_sha256"},
        "Engine Kit release identity fields are incomplete",
    )
    runtime_policy = data.get("runtime_policy", {})
    _require(
        runtime_policy.get("runner_workspace") == "{campaign_id}/runner-workspace"
        and runtime_policy.get("runner_environment") == "{campaign_id}/runner-workspace/.venv"
        and runtime_policy.get("pair_workspace") == "{campaign_id}/runner-workspace/{pair_id}/attempt-{attempt}",
        "campaign-owned runner workspace layout changed",
    )


def load_campaign_config(path: Path | str = DEFAULT_CONFIG) -> CampaignConfig:
    resolved = Path(path).resolve()
    raw = resolved.read_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid campaign JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("campaign configuration must be a JSON object")
    _validate(data)
    return CampaignConfig(path=resolved, data=data, content_sha256=sha256_bytes(raw))
