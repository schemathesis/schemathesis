import pytest


@pytest.mark.operations("basic")
def test_warning_on_unauthorized(cli, openapi3_schema_url, snapshot_cli):
    # When endpoint returns only 401
    # Then the output should contain a warning about it
    assert (
        cli.run(
            openapi3_schema_url,
            "-c not_a_server_error",
            "--mode=positive",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
        )
        == snapshot_cli
    )


@pytest.mark.operations("always_incorrect")
def test_warning_on_no_2xx(cli, openapi3_schema_url, snapshot_cli):
    # When endpoint does not return 2xx at all
    # Then the output should contain a warning about it
    assert (
        cli.run(
            openapi3_schema_url,
            "-c not_a_server_error",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
        )
        == snapshot_cli
    )


@pytest.mark.operations("always_incorrect")
def test_warning_on_no_2xx_options_only(cli, openapi3_schema_url, snapshot_cli):
    assert (
        cli.run(
            openapi3_schema_url,
            "--mode=all",
            "--phases=coverage",
            "-c not_a_server_error",
            "--phases=coverage",
            "--mode=negative",
            "-n 10",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ["args", "kwargs"],
    [
        ((), {}),
        (("--warnings=off",), {}),
        (("--warnings=missing_test_data",), {}),
        (
            (),
            {
                "config": {
                    "operations": [
                        {"include-name": "GET /failure", "warnings": False},
                    ],
                }
            },
        ),
    ],
    ids=["default", "selected", "disabled-all", "disabled-operation"],
)
@pytest.mark.operations("success", "failure")
def test_warning_on_all_not_found(cli, openapi3_schema_url, openapi3_base_url, snapshot_cli, args, kwargs):
    # When all endpoints return 404
    # Then the output should contain a warning about it
    assert (
        cli.run(
            openapi3_schema_url,
            f"--url={openapi3_base_url}/v4/",
            "-c not_a_server_error",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
            *args,
            **kwargs,
        )
        == snapshot_cli
    )


@pytest.mark.operations("success", "failure", "multiple_failures", "custom_format")
def test_warning_on_many_operations(cli, openapi3_schema_url, openapi3_base_url, snapshot_cli):
    assert (
        cli.run(
            openapi3_schema_url,
            f"--url={openapi3_base_url}/v4/",
            "-c not_a_server_error",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
        )
        == snapshot_cli
    )
