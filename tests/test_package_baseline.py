from pathlib import Path

import backgammon_engine_kit


ROOT = Path(__file__).resolve().parents[1]


def _metadata_value(name):
    for line in (ROOT / "setup.cfg").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + " = "):
            return line.split(" = ", 1)[1]
    raise AssertionError("missing metadata value: {}".format(name))


def test_package_version_matches_distribution_metadata():
    assert backgammon_engine_kit.__version__ == _metadata_value("version")


def test_declared_python_floor_matches_ankigammon_dependency():
    assert _metadata_value("python_requires") == ">=3.8"


def test_package_schema_resources_are_package_relative():
    schema_dir = ROOT / "src" / "backgammon_engine_kit" / "position_contract" / "schemas"
    assert sorted(path.name for path in schema_dir.glob("*.json")) == [
        "backgammon-view-v1.schema.json",
        "position-source-v1.schema.json",
        "universal-position-v1.schema.json",
    ]


def test_readme_universal_position_example_uses_the_public_api():
    decoded = backgammon_engine_kit.decode_xgid(
        "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10"
    )
    assert decoded.position.schema_version == "universal-position-v1"
    assert decoded.source.schema_version == "position-source-v1"


def test_package_sources_do_not_embed_private_absolute_paths():
    package_root = ROOT / "src" / "backgammon_engine_kit"
    for path in package_root.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json"}:
            text = path.read_text(encoding="utf-8")
            assert "C:\\Users\\" not in text
            assert "/home/" not in text
