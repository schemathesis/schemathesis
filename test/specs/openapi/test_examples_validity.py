import pytest
from hypothesis import given, settings

from schemathesis.specs.openapi.examples import get_strategies_from_examples


@pytest.mark.hypothesis_nested
def test_examples_validity(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/success": {
                "get": {
                    "parameters": [
                        {"name": "anyKey", "in": "header", "schema": {"type": "string"}},
                        {"name": "id", "in": "query", "schema": {"type": "string", "example": "1"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        servers=[{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
    )

    operation = next(schema.get_all_operations()).ok()
    strategy = get_strategies_from_examples(operation)[0]

    @given(case=strategy)
    @settings(deadline=None)
    def test(case):
        # Generated examples should have valid parameters
        # E.g. headers should be latin-1 encodable
        case.call(base_url=f"{api.base_url}/api")

    test()
