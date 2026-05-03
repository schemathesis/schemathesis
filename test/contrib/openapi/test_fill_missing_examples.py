def test_fills_missing_examples(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert (
        cli.run(api.schema_url, "--phases=examples", config={"phases": {"examples": {"fill-missing": True}}})
        == snapshot_cli
    )
