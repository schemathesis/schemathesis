import pytest

import schemathesis
from schemathesis.specs.openapi.references import resolve_pointer

RECURSIVE_SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
    "components": {
        "schemas": {
            "Node": {
                "type": "object",
                "required": ["child"],
                "properties": {"child": {"$ref": "#/components/schemas/Node"}},
            }
        }
    },
    "paths": {
        "/foo": {
            "post": {
                "summary": "Test",
                "description": "",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {},
                    }
                },
            }
        }
    },
}


@pytest.mark.benchmark
def test_inlining_during_resolution():
    schema = schemathesis.from_dict(RECURSIVE_SCHEMA)
    schema.resolver.resolve_all(
        RECURSIVE_SCHEMA["paths"]["/foo"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    )


@pytest.mark.benchmark
def test_resolve_pointer():
    resolve_pointer(RECURSIVE_SCHEMA, "/paths/~1foo/post/requestBody/content/application~1json/schema")
