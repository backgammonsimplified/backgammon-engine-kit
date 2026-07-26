"""Verified BGSage 1.2.20260706 runtime and public configuration identity."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..models import EngineConfiguration


SAGE_ENGINE_VERSION = "1.2.20260706"
SAGE_PARSER_VERSION = "bgsage-json-parser-v1"
SAGE_PROTOCOL_VERSION = "bgsage-position-analysis-v1"
SAGE_PROFILE = "bgsage-1.2.20260706-stage9-1ply-cubeful"
SAGE_INVOCATION_IDENTITY = "bgsage-fresh-process-json-stdin-v1"
SAGE_MODEL_NAME = "stage9"
SAGE_MODEL_AGGREGATE_SHA256 = "61dee35eb2a6ee974eefb6d8a0fa1c7270d0042e038cf11fcaac78b62f6a26e3"
SAGE_MODEL_IDENTITY = "stage9:sha256:" + SAGE_MODEL_AGGREGATE_SHA256
SAGE_BEAROFF_SHA256 = "907bbe0042dadfa14840a9f470b6b5adac213d5b2c17dc0e41aec27b087e3fe6"
SAGE_NATIVE_SHA256 = "1b0449938243478916f2ab459b8525a7b9d8f73c3f74fbc5f43a34c1d7b54c12"
SAGE_PYTHON_SHA256 = "6d972cf21be56fe3c947ab6ba257ff8d08c342dd2714442986791bd9a6dfabfe"
SAGE_GNUID_PARSER_SHA256 = "fadf04cc08033a297c683a3d8ccc9c53bcfae4743cffe6e0c8d6e3e5d014436c"


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def model_aggregate_sha256(models_dir):
    digest = hashlib.sha256()
    paths = sorted(path for path in Path(models_dir).iterdir() if path.is_file())
    for path in paths:
        line = "{}  {}\n".format(file_sha256(path), path.name)
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def verified_sage_configuration():
    return EngineConfiguration(
        engine="sage",
        profile=SAGE_PROFILE,
        engine_version=SAGE_ENGINE_VERSION,
        model_or_weights_identity=SAGE_MODEL_IDENTITY,
        invocation_identity=SAGE_INVOCATION_IDENTITY,
        parser_version=SAGE_PARSER_VERSION,
        options=(
            ("actual_evaluation_type", "neural-network-evaluation"),
            ("analysis_ply", 1),
            ("bearoff_sha256", SAGE_BEAROFF_SHA256),
            ("beaver", False),
            ("candidate_generation", "all-legal-moves"),
            ("cubeful", True),
            ("deterministic", True),
            ("include_game_plans", False),
            ("jacoby", False),
            ("model", SAGE_MODEL_NAME),
            ("move_filter", "not-applicable-at-1ply"),
            ("noise", "not-exposed-at-1ply"),
            ("pruning", "not-exposed-at-1ply"),
            ("protocol", SAGE_PROTOCOL_VERSION),
            ("seed", 42),
            ("threads", 1),
        ),
    )


@dataclass(frozen=True)
class SageRuntimeConfiguration:
    """Private runtime paths; only content identities are made public."""

    python_executable: Path
    protocol_script: Path
    package_dir: Path
    native_module: Path
    gnuid_parser: Path

    def __post_init__(self):
        for name in (
            "python_executable",
            "protocol_script",
            "package_dir",
            "native_module",
            "gnuid_parser",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)))

    @classmethod
    def discover(cls):
        python = Path(
            os.environ.get("BGSAGE_PYTHON", str(Path.home() / "bg-env" / "bin" / "python3"))
        )
        site_packages = python.parent.parent / "lib" / "python3.11" / "site-packages"
        package_dir = site_packages / "bgsage"
        native_module = site_packages / "bgbot_cpp.cpython-311-x86_64-linux-gnu.so"
        gnuid_parser = site_packages / "ankigammon" / "utils" / "gnuid.py"
        return cls(python, Path(__file__).with_name("protocol.py"), package_dir, native_module, gnuid_parser)

    @property
    def models_dir(self):
        return self.package_dir / "_assets" / "models"

    @property
    def bearoff_path(self):
        return self.package_dir / "_assets" / "data" / "bearoff_1sided.db"

    def environment(self):
        return {
            "BGBOT_MULTIPLY_THREADS": "1",
            "HOME": "/dev/null",
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONHASHSEED": "0",
        }

    def validate_files(self):
        if not self.python_executable.is_file() or not os.access(str(self.python_executable), os.X_OK):
            raise FileNotFoundError("BGSage Python executable is unavailable")
        if not self.protocol_script.is_file():
            raise FileNotFoundError("BGSage protocol script is unavailable")
        if not self.package_dir.is_dir() or not self.models_dir.is_dir():
            raise FileNotFoundError("BGSage model directory is unavailable")
        for path, expected, label in (
            (self.python_executable, SAGE_PYTHON_SHA256, "Python executable"),
            (self.native_module, SAGE_NATIVE_SHA256, "native module"),
            (self.gnuid_parser, SAGE_GNUID_PARSER_SHA256, "GNU-ID parser"),
            (self.bearoff_path, SAGE_BEAROFF_SHA256, "bearoff database"),
        ):
            if not path.is_file():
                raise FileNotFoundError("required BGSage {} is unavailable".format(label))
            if file_sha256(path) != expected:
                raise ValueError("BGSage {} identity changed".format(label))
        if model_aggregate_sha256(self.models_dir) != SAGE_MODEL_AGGREGATE_SHA256:
            raise ValueError("BGSage model identity changed")

    def public_identity(self):
        return {
            "python": {"name": "python3.11", "sha256": SAGE_PYTHON_SHA256},
            "native_module": {"name": "bgbot_cpp", "sha256": SAGE_NATIVE_SHA256},
            "model": {"name": SAGE_MODEL_NAME, "sha256": SAGE_MODEL_AGGREGATE_SHA256},
            "bearoff": {"name": "bearoff_1sided.db", "sha256": SAGE_BEAROFF_SHA256},
            "gnuid_parser": {"name": "ankigammon-gnuid-1.7.0", "sha256": SAGE_GNUID_PARSER_SHA256},
        }
