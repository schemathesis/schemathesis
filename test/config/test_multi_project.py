def test_disable_phases(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(
            api.schema_url,
            "--checks=not_a_server_error",
            config={
                "project": [
                    {
                        "title": "Test",
                        "operations": [
                            {
                                "include-name": "GET /api/success",
                                "phases": {
                                    "fuzzing": {"enabled": False},
                                },
                            },
                        ],
                    }
                ],
                "operations": [
                    {
                        "include-name": "GET /api/failure",
                        "phases": {
                            "coverage": {"enabled": False},
                        },
                    },
                ],
            },
        )
        == snapshot_cli
    )


def test_disable_operations(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(
            api.schema_url,
            "--checks=not_a_server_error",
            config={
                "project": [
                    {
                        "title": "Test",
                        "operations": [
                            {"include-name": "GET /api/success", "enabled": False},
                        ],
                    }
                ],
            },
        )
        == snapshot_cli
    )
