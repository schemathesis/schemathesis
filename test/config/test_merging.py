from operator import attrgetter

import pytest

from schemathesis.config import (
    ChecksConfig,
    NotAServerErrorConfig,
    PositiveDataAcceptanceConfig,
    SchemathesisConfig,
    SimpleCheckConfig,
)


@pytest.mark.parametrize(
    "configs, expected",
    [
        # Empty list should return default config
        ([], ChecksConfig()),
        # Single config should return that config
        (
            [ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False))],
            ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False)),
        ),
        # Basic merging - first config takes precedence
        (
            [
                ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False)),
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(enabled=True),
                    status_code_conformance=SimpleCheckConfig(enabled=False),
                ),
            ],
            ChecksConfig(
                not_a_server_error=NotAServerErrorConfig(enabled=False),
                status_code_conformance=SimpleCheckConfig(enabled=False),
            ),
        ),
        # Merging nested attributes - first config's explicit attributes take precedence
        (
            [
                ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False)),
                ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=True, expected_statuses=[200, 201])),
            ],
            ChecksConfig(not_a_server_error=NotAServerErrorConfig(enabled=False, expected_statuses=[200, 201])),
        ),
        # Multi-level configuration hierarchy (override -> project -> defaults)
        (
            [
                # Override
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(enabled=False),
                ),
                # Project level config
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(expected_statuses=[200, 201]),
                    positive_data_acceptance=PositiveDataAcceptanceConfig(enabled=False),
                ),
                # Defaults
                ChecksConfig(
                    not_a_server_error=NotAServerErrorConfig(enabled=True),
                    status_code_conformance=SimpleCheckConfig(enabled=False),
                ),
            ],
            ChecksConfig(
                not_a_server_error=NotAServerErrorConfig(enabled=False, expected_statuses=[200, 201]),
                status_code_conformance=SimpleCheckConfig(enabled=False),
                positive_data_acceptance=PositiveDataAcceptanceConfig(enabled=False),
            ),
        ),
    ],
    ids=["empty", "single", "first-wins", "nested-merge", "override-project-defaults"],
)
def test_checks_config_from_hierarchy(configs, expected):
    assert ChecksConfig.from_hierarchy(configs) == expected


def test_checks_config_from_hierarchy_preserves_custom_check_config():
    file_config = ChecksConfig.from_dict({"CustomCheck": {"enabled": False, "threshold": 0.9}})
    cli_override = ChecksConfig()

    merged = ChecksConfig.from_hierarchy([cli_override, file_config])

    assert merged.get_by_name(name="CustomCheck").enabled is False
    assert merged.custom_kwargs["CustomCheck"] == {"threshold": 0.9}


TITLE = "Example API"
SCHEMA = {"info": {"title": TITLE}}


@pytest.mark.parametrize(
    ("top_level", "project", "read", "expected"),
    [
        (
            {"phases": {"coverage": {"enabled": False}}},
            {"phases": {"coverage": {"enabled": True}}},
            "phases.coverage.enabled",
            True,
        ),
        (
            {"phases": {"stateful": {"max-steps": 20}}},
            {"phases": {"stateful": {"max-steps": 6}}},
            "phases.stateful.max_steps",
            6,
        ),
        ({"workers": 4}, {"workers": 1}, "workers", 1),
        ({"tls-verify": False}, {"tls-verify": True}, "tls_verify", True),
        (
            {"generation": {"max-examples": 5}},
            {"generation": {"max-examples": 7}},
            "generation.max_examples",
            7,
        ),
    ],
    ids=[
        "coverage-enabled",
        "stateful-max-steps",
        "workers",
        "tls-verify",
        "control-neither-is-default",
    ],
)
def test_project_block_wins_over_top_level(top_level, project, read, expected):
    config = SchemathesisConfig.from_dict({**top_level, "project": [{"title": TITLE, **project}]})

    assert attrgetter(read)(config.projects.get(SCHEMA)) == expected


def test_cli_override_wins_over_config_file():
    config = SchemathesisConfig.from_dict({"workers": 4})
    config.projects.override.update(workers=1)

    assert config.projects.get_default().workers == 1


def test_first_matching_operation_block_wins(ctx):
    schema = ctx.openapi.load_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    config = SchemathesisConfig.from_dict(
        {
            "operations": [
                {"include-name": "GET /users", "enabled": True},
                {"include-name": "GET /users", "enabled": False},
            ]
        }
    )

    assert config.projects.get_default().operations.get_for_operation(schema["/users"]["GET"]).enabled is True
