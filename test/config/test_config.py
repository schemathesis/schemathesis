from pathlib import Path

import pytest

from schemathesis.config import ConfigError, SchemathesisConfig

CONFIGS_DIR = Path(__file__).parent / "configs"


def get_all_config_files(*subdirectories: str) -> dict[str, Path]:
    """Discover all TOML config files."""
    result = {}
    for subdir in subdirectories:
        directory = CONFIGS_DIR / subdir
        for f in directory.glob("*.toml"):
            result[f"{subdir}.{f.stem}"] = f
    return result


ALL_CONFIGS = get_all_config_files("common", "report", "parameters", "operations")


@pytest.mark.parametrize(
    "path",
    list(ALL_CONFIGS.values()),
    ids=list(ALL_CONFIGS),
)
def test_configs(monkeypatch, path, snapshot_config):
    monkeypatch.setenv("TEST_STRING_1", "foo")
    monkeypatch.setenv("TEST_STRING_2", "bar")
    try:
        assert SchemathesisConfig.from_path(path) == snapshot_config
    except ConfigError as exc:
        assert str(exc) == snapshot_config
