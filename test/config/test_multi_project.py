import pytest


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "failure")
def test_disable_phases(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--checks=not_a_server_error",
            config={
                "project": [
                    {
                        "title": "Example API",
                        "operations": [
                            {
                                "include-name": "GET /success",
                                "phases": {
                                    "fuzzing": {"enabled": False},
                                },
                            },
                        ],
                    }
                ],
                "operations": [
                    {
                        "include-name": "GET /failure",
                        "phases": {
                            "coverage": {"enabled": False},
                        },
                    },
                ],
            },
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "failure")
def test_disable_operations(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--checks=not_a_server_error",
            config={
                "project": [
                    {
                        "title": "Example API",
                        "operations": [
                            {"include-name": "GET /success", "enabled": False},
                        ],
                    }
                ],
            },
        )
        == snapshot_cli
    )
