from pathlib import Path

import pytest

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.config._operations import OperationConfig
from schemathesis.config._projects import ProjectConfig
from schemathesis.config._validator import CONFIG_SCHEMA
from schemathesis.core.errors import HookError

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
    except HookError as exc:
        assert str(exc) == snapshot_config


def test_warnings_for_without_operations():
    config = SchemathesisConfig.from_dict({"warnings": False})
    assert config.projects.default.warnings_for(operation=None).display == []


def test_project_key_config_sync():
    ignored_in_operations_config = {"operations", "hooks", "workers", "base_url"}
    for key in ProjectConfig.__slots__:
        if key.startswith("_"):
            continue
        property_name = key.replace("_", "-")
        assert property_name in CONFIG_SCHEMA["$defs"]["ProjectConfig"]["properties"]
        assert property_name in CONFIG_SCHEMA["properties"]
        if key not in ignored_in_operations_config:
            assert property_name in CONFIG_SCHEMA["$defs"]["OperationConfig"]["properties"]
            assert key in OperationConfig.__slots__
    for key in ignored_in_operations_config:
        property_name = key.replace("_", "-")
        assert property_name not in CONFIG_SCHEMA["$defs"]["OperationConfig"]["properties"]
        assert key not in OperationConfig.__slots__


def test_config_path_from_path(tmp_path):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("color = true\n")

    config = SchemathesisConfig.from_path(config_file)

    assert config.config_path == str(config_file.resolve())


def test_config_path_from_discover(tmp_path, monkeypatch):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("color = true\n")

    monkeypatch.chdir(tmp_path)
    config = SchemathesisConfig.discover()

    assert config.config_path == str(config_file.resolve())


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SchemathesisConfig(),
        lambda: SchemathesisConfig.from_dict({"color": True}),
    ],
    ids=["default", "from_dict"],
)
def test_config_path_none_when_no_file(factory):
    config = factory()
    assert config.config_path is None


def test_project_config_path_delegates_to_parent(tmp_path):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("color = true\n")

    config = SchemathesisConfig.from_path(config_file)
    project_config = config.projects.default

    assert project_config.config_path == str(config_file.resolve())


def test_project_config_path_none_without_parent():
    project_config = ProjectConfig()
    assert project_config.config_path is None
