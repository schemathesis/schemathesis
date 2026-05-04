import click


def test_base_url_not_truncated_on_narrow_terminal(ctx, cli):
    schema_path = ctx.openapi.write_schema({})
    long_url = "https://internal.staging.example.com/api/v3/internal/prefix/of/something-very-long"
    result = cli.run(
        str(schema_path),
        f"--url={long_url}",
        env={"PYTEST_VERSION": None, "COLUMNS": "80"},
    )
    assert long_url in "".join(click.unstyle(result.output).split())


def test_cli_displays_config_path(ctx, cli, app_runner, snapshot_cli):
    paths = {
        "/test": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    schema = ctx.openapi.build_schema(paths)
    app = ctx.openapi.make_permissive_flask_app(schema)
    port = app_runner.run_flask_app(app)
    schema_path = ctx.openapi.write_schema(paths)

    # Run with config parameter - cli fixture will write config file
    assert (
        cli.run(
            str(schema_path),
            f"--url=http://127.0.0.1:{port}/api",
            "-c",
            "not_a_server_error",
            "--max-examples=1",
            config={"headers": {"X-Test": "value"}},
        )
        == snapshot_cli
    )


def test_cli_no_config_display_without_file(ctx, cli, app_runner, snapshot_cli):
    paths = {
        "/test": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    schema = ctx.openapi.build_schema(paths)
    app = ctx.openapi.make_permissive_flask_app(schema)
    port = app_runner.run_flask_app(app)
    schema_path = ctx.openapi.write_schema(paths)

    # Run without config parameter - no config file used
    assert (
        cli.run(
            str(schema_path),
            f"--url=http://127.0.0.1:{port}/api",
            "-c",
            "not_a_server_error",
            "--max-examples=1",
        )
        == snapshot_cli
    )
