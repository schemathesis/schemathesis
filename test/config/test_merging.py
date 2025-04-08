from dataclasses import dataclass

import pytest

from schemathesis.config._checks import (
    ChecksConfig,
    NotAServerErrorConfig,
    PositiveDataAcceptanceConfig,
    SimpleCheckConfig,
)
from schemathesis.config._projects import ConfigOverride, ProjectConfig


@pytest.mark.parametrize(
    "configs, expected",
    [
        # Empty list should return default config
        ([], ChecksConfig()),
        # Single config should return that config
        (
            [ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False, _explicit_attrs={"enabled"}))],
            ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False, _explicit_attrs={"enabled"})),
        ),
        # Basic merging - first config takes precedence
        (
            [
                ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False, _explicit_attrs={"enabled"})),
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(enabled=True, _explicit_attrs={"enabled"}),
                    status_code_conformance=SimpleCheckConfig(enabled=False, _explicit_attrs={"enabled"}),
                ),
            ],
            ChecksConfig(
                not_a_server_error=NotAServerErrorConfig(enabled=False, _explicit_attrs={"enabled"}),
                status_code_conformance=SimpleCheckConfig(enabled=False, _explicit_attrs={"enabled"}),
            ),
        ),
        # Merging nested attributes - first config's explicit attributes take precedence
        (
            [
                ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False, _explicit_attrs={"enabled"})),
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(
                        enabled=True, expected_statuses=[200, 201], _explicit_attrs={"enabled", "expected_statuses"}
                    )
                ),
            ],
            ChecksConfig(
                not_a_server_error=NotAServerErrorConfig(
                    enabled=False, expected_statuses=[200, 201], _explicit_attrs={"enabled", "expected_statuses"}
                )
            ),
        ),
        # Multi-level configuration hierarchy (override → project → defaults)
        (
            [
                # Override
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(enabled=False, _explicit_attrs={"enabled"}),
                ),
                # Project level config
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(
                        expected_statuses=[200, 201], _explicit_attrs={"expected_statuses"}
                    ),
                    positive_data_acceptance=PositiveDataAcceptanceConfig(enabled=False, _explicit_attrs={"enabled"}),
                ),
                # Defaults
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(enabled=True, _explicit_attrs={"enabled"}),
                    status_code_conformance=SimpleCheckConfig(enabled=False, _explicit_attrs={"enabled"}),
                ),
            ],
            ChecksConfig(
                not_a_server_error=NotAServerErrorConfig(
                    enabled=False, expected_statuses=[200, 201], _explicit_attrs={"enabled", "expected_statuses"}
                ),
                status_code_conformance=SimpleCheckConfig(enabled=False, _explicit_attrs={"enabled"}),
                positive_data_acceptance=PositiveDataAcceptanceConfig(enabled=False, _explicit_attrs={"enabled"}),
            ),
        ),
    ],
)
def test_checks_config_from_many(configs, expected):
    assert ChecksConfig.from_many(configs) == expected


@dataclass
class APIOperation:
    path: str
    method: str


@pytest.fixture
def basic_operation():
    return APIOperation(path="/users", method="GET")


@pytest.fixture
def project_config():
    return ProjectConfig.from_dict({"checks": {"not_a_server_error": {"enabled": True}}})


def test_project_level_checks_config(project_config):
    result = project_config.checks_config_for()

    assert result.not_a_server_error.enabled is True
    assert "enabled" in result.not_a_server_error._explicit_attrs


def test_phase_specific_configuration():
    project_config = ProjectConfig.from_dict(
        {
            "checks": {"not_a_server_error": {"enabled": True}},
            "phases": {"fuzzing": {"checks": {"not_a_server_error": {"enabled": False}}}},
        }
    )

    result = project_config.checks_config_for(phase="fuzzing")
    assert result.not_a_server_error.enabled is False


def test_operation_matching(basic_operation):
    project_config = ProjectConfig.from_dict(
        {
            "checks": {"not_a_server_error": {"enabled": True}},
            "operations": [
                {"paths": ["/users"], "methods": ["GET"], "checks": {"not_a_server_error": {"enabled": False}}}
            ],
        }
    )

    result = project_config.checks_config_for(operation=basic_operation)
    assert result.not_a_server_error.enabled is False


def test_operation_not_matching(basic_operation):
    project_config = ProjectConfig.from_dict(
        {
            "checks": {"not_a_server_error": {"enabled": True}},
            "operations": [
                {
                    "include-path": ["/items"],
                    "include-method": ["POST"],
                    "checks": {"not_a_server_error": {"enabled": False}},
                }
            ],
        }
    )

    result = project_config.checks_config_for(operation=basic_operation)
    assert result.not_a_server_error.enabled is True


def test_operation_and_phase_configuration(basic_operation):
    project_config = ProjectConfig.from_dict(
        {
            "checks": {"not_a_server_error": {"enabled": True}},
            "operations": [
                {
                    "include-path": ["/users"],
                    "include-method": ["GET"],
                    "checks": {"not_a_server_error": {"enabled": False}},
                    "phases": {
                        "fuzzing": {"checks": {"not_a_server_error": {"enabled": True, "expected-statuses": [200]}}}
                    },
                }
            ],
        }
    )

    result = project_config.checks_config_for(operation=basic_operation, phase="fuzzing")

    assert result.not_a_server_error.enabled is True
    assert result.not_a_server_error.expected_statuses == ["200"]


def test_override_configuration(basic_operation):
    project_config = ProjectConfig.from_dict(
        {
            "checks": {"not_a_server_error": {"enabled": True}},
            "operations": [
                {
                    "include-path": ["/users"],
                    "include-method": ["GET"],
                    "checks": {"not_a_server_error": {"enabled": False}},
                }
            ],
            "phases": {"fuzzing": {"checks": {"status_code_conformance": {"enabled": False}}}},
        }
    )

    override_checks = ChecksConfig.from_dict(
        {
            "not_a_server_error": {
                "enabled": True,
                "expected-statuses": [418],
            }
        }
    )
    project_config._override = ConfigOverride(checks=override_checks)

    result = project_config.checks_config_for(operation=basic_operation, phase="fuzzing")

    assert result.not_a_server_error.enabled is True
    assert result.not_a_server_error.expected_statuses == ["418"]
    assert result.status_code_conformance.enabled is False


def test_multiple_matching_operations(basic_operation):
    project_config = ProjectConfig.from_dict(
        {
            "checks": {"not_a_server_error": {"enabled": True}},
            "operations": [
                {
                    "include-path": ["/users"],
                    "include-method": ["GET"],
                    "checks": {"not_a_server_error": {"enabled": False}},
                },
                {
                    "include-method": ["GET"],
                    "checks": {"status_code_conformance": {"enabled": False}},
                },
            ],
        }
    )

    result = project_config.checks_config_for(operation=basic_operation)
    assert result.not_a_server_error.enabled is False
    assert result.status_code_conformance.enabled is False
