import pytest
from hypothesis import given

import schemathesis
from schemathesis._hypothesis import get_example


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
                            {"name": "anyKey", "in": "header", "schema": {"type": "string"},},
                            {"name": "id", "in": "query", "schema": {"type": "string", "example": "1"},},
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )


@pytest.mark.hypothesis_nested
def test_examples_validity(schema, base_url):
    strategy = get_example(schema.endpoints["/api/success"]["get"])

    @given(case=strategy)
    def test(case):
        # Generated examples should have valid parameters
        # E.g. headers should be latin-1 encodable
        case.call(base_url=base_url)

    test()
