import pytest

from schemathesis.config import (
    ChecksConfig,
    NotAServerErrorConfig,
    PositiveDataAcceptanceConfig,
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
        # Multi-level configuration hierarchy (override → project → defaults)
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
)
def test_checks_config_from_hierarchy(configs, expected):
    assert ChecksConfig.from_hierarchy(configs) == expected
