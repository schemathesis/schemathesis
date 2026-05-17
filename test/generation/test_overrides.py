from schemathesis.config import SchemathesisConfig
from schemathesis.generation.overrides import for_operation


def test_qualified_key_wins_over_bare(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "user_id", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    parent = SchemathesisConfig.from_dict({"parameters": {"user_id": 1, "query.user_id": 2}})
    schema.config._parent = parent
    schema.config.parameters = parent.projects.default.parameters
    assert for_operation(schema.config, operation=schema["/items"]["GET"]).query == {"user_id": 2}
