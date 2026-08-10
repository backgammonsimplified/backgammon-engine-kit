from pathlib import Path

import pytest

from backgammon_engine_kit.models import EngineConfiguration, RawSource
from backgammon_engine_kit.serialization import ensure_public_safe


def test_private_absolute_path_exclusion():
    with pytest.raises(ValueError, match="private absolute path"):
        EngineConfiguration(engine="sage", profile="/home/private-user/engine-profile")
    with pytest.raises(ValueError, match="private absolute path"):
        RawSource.from_output("read from /users/private-user/runtime/output.txt")


def test_secret_like_value_exclusion():
    with pytest.raises(ValueError, match="secret-like value"):
        EngineConfiguration(
            engine="sage",
            profile="test",
            options=(("header", "api_key=dummy-value"),),
        )
    with pytest.raises(ValueError, match="secret-like value"):
        EngineConfiguration(
            engine="sage",
            profile="test",
            options=(("password", "dummy-value"),),
        )


def test_fixture_files_exclude_private_paths_and_secret_like_values():
    root = Path(__file__).resolve().parents[1] / "fixtures"
    for path in root.rglob("*"):
        if path.is_file():
            ensure_public_safe(path.read_text(encoding="utf-8"), str(path.name))
