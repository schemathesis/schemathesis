import pytest

import schemathesis


@pytest.mark.benchmark
def test_schema_resolution():
    raw_schema = {
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

    schema = schemathesis.from_dict(raw_schema)
    schema.resolver.resolve_all(
        raw_schema["paths"]["/foo"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    )
