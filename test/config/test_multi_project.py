import schemathesis
from schemathesis.config import SchemathesisConfig


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


def test_project_operations_take_precedence_over_global_operations(ctx):
    raw_schema = ctx.openapi.build_schema({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})
    config = SchemathesisConfig.from_dict(
        {
            "operations": [{"include-path": "/items", "request-timeout": 1}],
            "project": [
                {
                    "title": raw_schema["info"]["title"],
                    "operations": [{"include-path": "/items", "request-timeout": 99}],
                }
            ],
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema, config=config)
    assert schema.config.request_timeout_for(operation=schema["/items"]["GET"]) == 99
