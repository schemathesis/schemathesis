import pytest

from schemathesis.specs.openapi.parameters import Example, OpenAPI20Parameter, OpenAPI30Parameter


@pytest.mark.parametrize(
    "cls, schema, expected",
    (
        (
            OpenAPI20Parameter,
            {"name": "id", "in": "query", "required": True, "type": "string", "x-example": "test"},
            [Example(None, "test")],
        ),
        (
            OpenAPI30Parameter,
            {"name": "id", "in": "query", "required": True, "type": "string", "example": "test"},
            [Example(None, "test")],
        ),
        (
            OpenAPI20Parameter,
            {"name": "id", "in": "body", "required": True, "schema": {"type": "string", "example": "test"}},
            [Example(None, "test")],
        ),
        (
            OpenAPI30Parameter,
            {"name": "id", "in": "query", "required": True, "schema": {"type": "string", "example": "test"}},
            [Example(None, "test")],
        ),
        (
            OpenAPI20Parameter,
            {"name": "id", "in": "query", "required": True, "type": "string", "x-examples": {"foo": "t1", "bar": "t2"}},
            [Example("foo", "t1"), Example("bar", "t2")],
        ),
        (
            OpenAPI30Parameter,
            {
                "name": "id",
                "in": "query",
                "required": True,
                "schema": {"type": "string"},
                "examples": {"foo": "t1", "bar": "t2"},
            },
            [Example("foo", "t1"), Example("bar", "t2")],
        ),
        # TODO. check if the examples are properly overridden
    ),
)
def test_examples(cls, schema, expected):
    assert list(cls(schema).iter_examples()) == expected
