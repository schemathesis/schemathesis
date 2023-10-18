import pytest

import schemathesis


@pytest.fixture
def loose_schema(empty_open_api_2_schema):
    empty_open_api_2_schema["paths"] = {
        "/test/{key}": {
            "post": {
                "parameters": [{"name": "key", "in": "path"}],
                "responses": {"default": {"description": "OK"}},
            }
        }
    }
    return schemathesis.from_dict(empty_open_api_2_schema, base_url="http://127.0.0.1:1", validate_schema=False)
