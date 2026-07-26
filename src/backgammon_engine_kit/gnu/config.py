"""Verified GNU 1.08.003 runtime and public configuration identity."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import shutil

from ..models import EngineConfiguration


GNU_ENGINE_VERSION = "1.08.003 20260710"
GNU_VERSION_LINE = "GNU Backgammon " + GNU_ENGINE_VERSION
GNU_PARSER_VERSION = "gnu-text-parser-v1"
GNU_PROFILE = "gnu-1.08.003-1ply-cubeful-noiseless"
GNU_INVOCATION_IDENTITY = "gnubg-cli-stdin-hint-v1"

EXECUTABLE_SHA256 = "caaa300e2ed0f1e4315979e87e15d884ad60f094f69dfc92b170dfaf1c9f8937"
RESOURCE_IDENTITIES = (
    ("gnubg.wd", "bb045bb416c70706ba34ad64595af2b43cac3722560b6ab01829eb719417153b"),
    ("gnubg_os0.bd", "38089567e87a681682bb4ff53f30d781af215fc04debbdff3f61b6db68547a49"),
    ("gnubg_ts0.bd", "9eac8a2c697dae8a09f2e5653022084b9e725df6c32950cb5299b273fc64500f"),
    ("met/Kazaross-XG2.xml", "7a232b171744b8db34306d11cff79a5974541328bb033b6bf16c012e8f7a3cc3"),
)
GNU_MODEL_IDENTITY = ";".join(
    "{}:sha256:{}".format(name, digest) for name, digest in RESOURCE_IDENTITIES
)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verified_gnu_configuration():
    """Return the exact public configuration supported by committed evidence."""
    return EngineConfiguration(
        engine="gnu",
        profile=GNU_PROFILE,
        engine_version=GNU_ENGINE_VERSION,
        model_or_weights_identity=GNU_MODEL_IDENTITY,
        invocation_identity=GNU_INVOCATION_IDENTITY,
        parser_version=GNU_PARSER_VERSION,
        options=(
            ("actual_evaluation_type", "evaluation"),
            ("beavers", 0),
            ("cubeful", True),
            ("deterministic", True),
            ("evaluation_plies", 1),
            ("jacoby", False),
            ("move_filter", "1:0:0:8:0.160"),
            ("noise", 0.0),
            ("output_digits", 6),
            ("output_mwc", False),
            ("output_winpc", False),
            ("pruning", False),
            ("threads", 1),
            ("variation", "standard"),
        ),
    )


@dataclass(frozen=True)
class GnuRuntimeConfiguration:
    """Private runtime paths; only their public content identities may be serialized."""

    executable: Path
    data_dir: Path
    package_data_dir: Path

    def __post_init__(self):
        object.__setattr__(self, "executable", Path(self.executable))
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        object.__setattr__(self, "package_data_dir", Path(self.package_data_dir))

    @classmethod
    def discover(cls):
        executable = os.environ.get("GNUBG_EXECUTABLE") or shutil.which("gnubg")
        if not executable:
            raise FileNotFoundError("GNU Backgammon executable is unavailable")
        executable_path = Path(executable).resolve()
        prefix = executable_path.parent.parent
        data_dir = Path(os.environ.get("GNUBG_DATA_DIR", str(prefix / "share")))
        package_data_dir = Path(
            os.environ.get("GNUBG_PKGDATA_DIR", str(data_dir / "gnubg"))
        )
        return cls(executable_path, data_dir, package_data_dir)

    def environment(self):
        return {
            "HOME": "/dev/null",
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_NUM_THREADS": "1",
        }

    def validate_files(self):
        if not self.executable.is_file() or not os.access(str(self.executable), os.X_OK):
            raise FileNotFoundError("GNU Backgammon executable is unavailable")
        if file_sha256(self.executable) != EXECUTABLE_SHA256:
            raise ValueError("GNU Backgammon executable identity changed")
        for relative, expected in RESOURCE_IDENTITIES:
            resource = self.package_data_dir / relative
            if not resource.is_file():
                raise FileNotFoundError("required GNU resource is unavailable: {}".format(relative))
            if file_sha256(resource) != expected:
                raise ValueError("GNU resource identity changed: {}".format(relative))

    def public_identity(self):
        return {
            "executable": {"name": "gnubg", "sha256": EXECUTABLE_SHA256},
            "resources": [
                {"name": name, "sha256": digest} for name, digest in RESOURCE_IDENTITIES
            ],
        }
