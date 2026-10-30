from schemathesis.config import SchemathesisConfig
from schemathesis.generation.overrides import Override, for_operation


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


def test_applied_headers_stay_case_insensitive(ctx):
    operation = ctx.openapi.load_operation("GET /items", {"responses": {"200": {"description": "OK"}}})
    case = operation.Case()
    Override(headers={"Authorization": "Bearer token"}).apply_to(case)
    assert case.headers["authorization"] == "Bearer token"
