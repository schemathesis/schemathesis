import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.specs.openapi.examples import get_strategies_from_examples


@pytest.fixture(scope="module")
def schema():
    return schemathesis.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
            "paths": {
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
        }
    )


@pytest.mark.hypothesis_nested
def test_examples_validity(schema, openapi3_base_url):
    operation = next(schema.get_all_operations()).ok()
    strategy = get_strategies_from_examples(operation)[0]

    @given(case=strategy)
    @settings(deadline=None)
    def test(case):
        # Generated examples should have valid parameters
        # E.g. headers should be latin-1 encodable
        case.call(base_url=openapi3_base_url)

    test()
