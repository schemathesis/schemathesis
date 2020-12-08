import pytest

from schemathesis.specs.openapi.parameters import OpenAPI20Parameter, OpenAPI30Parameter


@pytest.mark.parametrize(
    "cls, schema, expected",
    (
        (
            OpenAPI20Parameter,
            {"name": "id", "in": "query", "required": True, "type": "string", "x-example": "test"},
            "test",
        ),
        (
            OpenAPI30Parameter,
            {"name": "id", "in": "query", "required": True, "type": "string", "example": "test"},
            "test",
        ),
        (
            OpenAPI20Parameter,
            {"name": "id", "in": "body", "required": True, "schema": {"type": "string", "example": "test"}},
            "test",
        ),
        (
            OpenAPI30Parameter,
            {"name": "id", "in": "query", "required": True, "schema": {"type": "string", "example": "test"}},
            "test",
        ),
    ),
)
def test_examples(cls, schema, expected):
    assert cls(schema).example == expected
