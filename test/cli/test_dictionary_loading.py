import pytest

from test.apps.catalog.openapi import nested


@pytest.mark.parametrize("pointer", ["/data/*/id", "/items/*/id"], ids=["loaded", "no-values"])
def test_dictionary_loading_output(ctx, cli, snapshot_cli, pointer):
    api = ctx.openapi.serve(nested.parents_with_children())
    config = {"dictionaries": {"parents": {"from-operation": "GET /api/parents", "pointer": pointer}}}
    assert cli.run(api.schema_url, "--phases=examples", config=config) == snapshot_cli
