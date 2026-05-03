def test_phase_configuration(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(
            api.schema_url,
            "--checks=not_a_server_error",
            config={
                "operations": [
                    {
                        "include-name": "GET /api/success",
                        "phases": {
                            "fuzzing": {"enabled": False},
                        },
                    },
                    {
                        "include-name": "GET /api/failure",
                        "phases": {
                            "coverage": {"enabled": False},
                        },
                    },
                ]
            },
        )
        == snapshot_cli
    )


def test_disable_all_and_enable_one(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(
            api.schema_url,
            "--checks=not_a_server_error",
            config={
                "phases": {"enabled": False, "fuzzing": {"enabled": True}},
            },
        )
        == snapshot_cli
    )
