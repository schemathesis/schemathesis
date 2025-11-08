def test_cli_displays_config_path(ctx, cli, openapi3_base_url, snapshot_cli):
    # Create schema file
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "get": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    # Run with config parameter - cli fixture will write config file
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "-c",
            "not_a_server_error",
            "--max-examples=1",
            config={"headers": {"X-Test": "value"}},
        )
        == snapshot_cli
    )


def test_cli_no_config_display_without_file(ctx, cli, openapi3_base_url, snapshot_cli):
    # Create schema file without config
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "get": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    # Run without config parameter - no config file used
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "-c",
            "not_a_server_error",
            "--max-examples=1",
        )
        == snapshot_cli
    )
