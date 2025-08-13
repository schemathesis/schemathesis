import pytest


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "failure")
def test_phase_configuration(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--checks=not_a_server_error",
            config={
                "operations": [
                    {
                        "include-name": "GET /success",
                        "phases": {
                            "fuzzing": {"enabled": False},
                        },
                    },
                    {
                        "include-name": "GET /failure",
                        "phases": {
                            "coverage": {"enabled": False},
                        },
                    },
                ]
            },
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "failure")
def test_disable_all_and_enable_one(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--checks=not_a_server_error",
            config={
                "phases": {"enabled": False, "fuzzing": {"enabled": True}},
            },
        )
        == snapshot_cli
    )
