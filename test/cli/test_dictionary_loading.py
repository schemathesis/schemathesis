import pytest

from test.apps.catalog.openapi import nested

PARENTS = {"from-operation": "GET /api/parents", "pointer": "/data/*/id"}
CHILDREN = {
    "from-operation": "GET /api/parents/{parentId}/children",
    "pointer": "/data/*/id",
    "parameters": {"path.parentId": {"dictionary": "parents"}},
}


@pytest.mark.parametrize(
    "dictionaries",
    [
        {"parents": PARENTS},
        {"parents": {**PARENTS, "pointer": "/items/*/id"}},
        {"parents": PARENTS, "children": CHILDREN},
        {"parents": {"values": [1, 9]}, "children": CHILDREN},
    ],
    ids=["loaded", "no-values", "nested", "partial"],
)
def test_dictionary_loading_output(ctx, cli, snapshot_cli, dictionaries):
    api = ctx.openapi.serve(nested.parents_with_children())
    assert cli.run(api.schema_url, "--phases=examples", config={"dictionaries": dictionaries}) == snapshot_cli
