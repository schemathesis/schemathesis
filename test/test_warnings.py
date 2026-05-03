import pytest


def test_warning_on_unauthorized(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.basic()
    # When endpoint returns only 401
    # Then the output should contain a warning about it
    assert (
        cli.run(
            api.schema_url,
            "-c not_a_server_error",
            "--mode=positive",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
        )
        == snapshot_cli
    )


def test_warning_on_no_2xx(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.always_incorrect()
    # When endpoint does not return 2xx at all
    # Then the output should contain a warning about it
    assert (
        cli.run(
            api.schema_url,
            "-c not_a_server_error",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
        )
        == snapshot_cli
    )


def test_warning_on_no_2xx_options_only(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.always_incorrect()
    assert (
        cli.run(
            api.schema_url,
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
                        {"include-name": "GET /api/failure", "warnings": False},
                    ],
                }
            },
        ),
    ],
    ids=["default", "selected", "disabled-all", "disabled-operation"],
)
def test_warning_on_all_not_found(ctx, cli, snapshot_cli, args, kwargs):
    api = ctx.openapi.apps.success_and_failure()
    # When all endpoints return 404
    # Then the output should contain a warning about it
    assert (
        cli.run(
            api.schema_url,
            f"--url={api.base_url}/v4/",
            "-c not_a_server_error",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
            *args,
            **kwargs,
        )
        == snapshot_cli
    )


def test_warning_on_many_operations(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_failure_multiple_failures_custom_format()
    assert (
        cli.run(
            api.schema_url,
            f"--url={api.base_url}/v4/",
            "-c not_a_server_error",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 10",
        )
        == snapshot_cli
    )
