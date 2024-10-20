import pytest

import schemathesis


@pytest.fixture
def loose_schema(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test/{key}": {
                "post": {
                    "parameters": [{"name": "key", "in": "path"}],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    return schemathesis.from_dict(schema, base_url="http://127.0.0.1:1", validate_schema=False)
