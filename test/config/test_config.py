from pathlib import Path

import pytest

import schemathesis
from schemathesis.config import (
    ConfigError,
    OperationConfig,
    OperationsConfig,
    ProjectConfig,
    SchemathesisConfig,
    SchemathesisWarning,
    get_workers_count,
)
from schemathesis.config._phases import DEFAULT_UNEXPECTED_METHODS
from schemathesis.config._validator import CONFIG_SCHEMA
from schemathesis.errors import HookError
from schemathesis.filters import FilterSet

CONFIGS_DIR = Path(__file__).parent / "configs"


def get_all_config_files(*subdirectories: str) -> dict[str, Path]:
    """Discover all TOML config files."""
    result = {}
    for subdir in subdirectories:
        directory = CONFIGS_DIR / subdir
        for f in directory.glob("*.toml"):
            result[f"{subdir}.{f.stem}"] = f
    return result


ALL_CONFIGS = get_all_config_files("common", "report", "cache", "parameters", "operations", "fuzz", "dictionaries")
WARNING_NAMES = [warning.value for warning in SchemathesisWarning]


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$RdescudvJCsgt3ub",
            "$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$RdescudvJCsgt3ub",
        ),
        ("$TEST_STRING_1", "$TEST_STRING_1"),
        ("a$$b", "a$$b"),
        ("5 $", "5 $"),
        ("$${TEST_STRING_1}", "${TEST_STRING_1}"),
        ("${TEST_STRING_1}:$${TEST_STRING_1}", "foo:${TEST_STRING_1}"),
    ],
    ids=["password-hash", "bare-name", "double-dollar", "trailing-dollar", "escaped-placeholder", "mixed"],
)
def test_env_substitution_only_replaces_braced_placeholders(monkeypatch, value, expected):
    monkeypatch.setenv("TEST_STRING_1", "foo")
    config = SchemathesisConfig.from_dict({"parameters": {"body.password": value}})
    assert config.projects.default.parameters == {"body.password": expected}


def test_escaped_placeholder_is_resolved_once(monkeypatch):
    monkeypatch.delenv("TEST_STRING_1", raising=False)
    config = SchemathesisConfig.from_str("[auth.wfc]\npath = '$${TEST_STRING_1}.yaml'")
    assert repr(config.projects.default.auth.wfc) == "WFCAuthConfig(path='${TEST_STRING_1}.yaml')"


def test_warnings_for_without_operations():
    config = SchemathesisConfig.from_dict({"warnings": False})
    assert config.projects.default.warnings_for(operation=None).display == []


def test_unexpected_methods_accept_every_default_method():
    methods = sorted(method.upper() for method in DEFAULT_UNEXPECTED_METHODS)
    assert (
        SchemathesisConfig.from_dict(
            {"phases": {"coverage": {"unexpected-methods": methods}}}
        ).projects.default.phases.coverage.unexpected_methods
        == DEFAULT_UNEXPECTED_METHODS
    )


def test_project_key_config_sync():
    ignored_in_operations_config = {
        "operations",
        "hooks",
        "workers",
        "base_url",
        "origin",
        "fuzz",
        "analysis",
        "baseline",
    }
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


@pytest.mark.parametrize(
    ("project_analysis", "expected"),
    [
        ("[project.analysis.constants]\nenabled = true", True),
        ("", False),
    ],
    ids=["explicitly-enabled", "inherited"],
)
def test_project_constants_analysis_precedence(project_analysis, expected):
    config = SchemathesisConfig.from_str(
        f"""
        [analysis.constants]
        enabled = false

        [[project]]
        title = "Enabled API"
        {project_analysis}
        """
    )

    project = config.projects.get({"info": {"title": "Enabled API"}})

    assert project.analysis.constants.enabled is expected


def test_override_can_reenable_constants_analysis():
    config = SchemathesisConfig.from_str("[analysis.constants]\nenabled = false\n")

    config.projects.override.analysis.constants.enabled = True

    assert config.projects.get_default().analysis.constants.enabled is True


def test_resolved_constants_config_does_not_alias_project_config():
    config = SchemathesisConfig.from_str(
        """
        [[project]]
        title = "Enabled API"
        [project.analysis.constants]
        enabled = true
        """
    )

    project = config.projects.get({"info": {"title": "Enabled API"}})
    project.analysis.constants.enabled = False

    assert config.projects.named["Enabled API"].analysis.constants.enabled is True


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


def test_filter_set_with_returns_copy_when_no_operations():
    # See GH-3572.
    # filter_set_with() must return a copy when there are no `[[operations]]` configured.
    # This avoids mutations that can corrupt the execution flow on subsequent runs
    ops = OperationsConfig()
    original = FilterSet()
    original.exclude(path="/admin")

    assert ops.filter_set_with(include=original) is not original


@pytest.mark.parametrize(
    ("template", "select"),
    [
        ('warnings = ["{name}"]', lambda warnings: warnings.display),
        ('[warnings]\ndisplay = ["{name}"]', lambda warnings: warnings.display),
        ('[warnings]\nfail-on = ["{name}"]', lambda warnings: warnings.fail_on),
    ],
    ids=["shorthand", "display", "fail-on"],
)
@pytest.mark.parametrize("name", WARNING_NAMES, ids=WARNING_NAMES)
def test_every_warning_name_is_usable_in_config(name, template, select):
    config = SchemathesisConfig.from_str(template.format(name=name))

    assert select(config.projects.default.warnings_for(operation=None)) == [SchemathesisWarning.from_str(name)]


def test_warning_names_match_config_schema():
    assert CONFIG_SCHEMA["$defs"]["WarningName"]["enum"] == WARNING_NAMES


@pytest.mark.parametrize(
    ("source", "section", "description"),
    [
        ('warnings = ["mising_auth"]', "root", "Item #0 in the 'warnings' array"),
        ('[warnings]\ndisplay = ["mising_auth"]', "[warnings]", "Item #0 in the 'display' array"),
        ('[warnings]\nfail-on = ["mising_auth"]', "[warnings]", "Item #0 in the 'fail-on' array"),
    ],
    ids=["shorthand", "display", "fail-on"],
)
def test_unknown_warning_name_is_reported_with_a_suggestion(source, section, description):
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str(source)

    assert str(exc.value) == (
        f"Error in {section} section:\n  Invalid value:\n\n"
        f"  - {description} -> 'mising_auth' is not a valid value. Did you mean 'missing_auth'?\n\n"
        f"Valid values are: {', '.join(repr(name) for name in sorted(WARNING_NAMES))}."
    )


def test_generation_mode_all_and_maximize_list():
    generation = SchemathesisConfig.from_str(
        '[generation]\nmode = "all"\nmaximize = ["response_time"]\n'
    ).projects.default.generation

    assert (generation.modes, [metric.__name__ for metric in generation.maximize]) == (
        list(schemathesis.GenerationMode),
        ["response_time"],
    )


def test_warnings_disabled_in_table_form_hide_and_never_fail():
    warnings = SchemathesisConfig.from_str("[warnings]\nenabled = false\nfail-on = true\n").projects.default.warnings

    assert (warnings.display, warnings.fail_on) == ([], [])


def test_workers_auto_uses_available_cpus():
    config = SchemathesisConfig.from_str('workers = "auto"')
    override = config.projects.override
    override.update(workers="auto")

    assert (config.projects.default.workers, override.workers) == (get_workers_count(), get_workers_count())


@pytest.mark.parametrize("workers", [0, -1])
def test_workers_below_one(workers):
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str(f"workers = {workers}")
    assert str(exc.value) == (
        f"Invalid value for 'workers': {workers}\n\n"
        "Expected either:\n"
        "  - A positive integer (e.g., workers = 4)\n"
        '  - The string "auto" for automatic detection (workers = "auto")'
    )


def test_project_workers_below_one():
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str('[[project]]\ntitle = "a"\nworkers = 0')
    assert str(exc.value) == (
        "Error in [project.0] section:\n  Value out of range:\n\n  - 'workers' -> Must be at least 1, but got 0."
    )


def test_standalone_project_config_reads_discovered_config_file(tmp_path, monkeypatch):
    (tmp_path / "schemathesis.toml").write_text("seed = 42\n")
    monkeypatch.chdir(tmp_path)

    assert ProjectConfig().seed == 42


def test_run_limits_set_on_project_apply_to_whole_config():
    config = SchemathesisConfig()
    project = config.projects.default

    project.max_failures = 3
    project.max_time = 60
    project.seed = 7

    assert (config.max_failures, config.max_time, config.seed) == (3, 60, 7)


def test_custom_check_disabled_in_config():
    checks = SchemathesisConfig.from_str("[checks.custom_check]\nenabled = false\n").projects.default.checks

    assert repr(checks.get_by_name(name="custom_check")) == "SimpleCheckConfig(enabled=False)"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("[checks]\nenabled = false\n", False),
        ("[checks]\nenabled = false\n[checks.custom_check]\nthreshold = 1\n", False),
        ("[checks]\nenabled = false\n[checks.custom_check]\nenabled = true\n", True),
        ("[checks]\nenabled = false\n[phases.fuzzing.generation]\nmax-examples = 5\n", False),
    ],
    ids=["unconfigured", "kwargs-only", "re-enabled", "phase-merged"],
)
def test_global_checks_enabled_applies_to_custom_checks(source, expected):
    project = SchemathesisConfig.from_str(source).projects.default

    assert project.checks_config_for(phase="fuzzing").get_by_name(name="custom_check").enabled is expected


@pytest.mark.parametrize(
    ("override", "project", "expected"),
    [
        ({"excluded_check_names": ["not_a_server_error"]}, {"included_check_names": ["not_a_server_error"]}, False),
        ({"included_check_names": ["not_a_server_error"]}, {"excluded_check_names": ["not_a_server_error"]}, True),
    ],
    ids=["override-excludes", "override-includes"],
)
def test_override_check_selection_beats_project_selection(override, project, expected):
    config = SchemathesisConfig()
    config.projects.override.checks.update(**override)
    config.projects.default.checks.update(**project)

    assert config.projects.get_default().checks.not_a_server_error.enabled is expected
