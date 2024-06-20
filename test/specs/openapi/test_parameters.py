import pytest

from schemathesis.specs.openapi.parameters import (
    OpenAPI20Body,
    OpenAPI20CompositeBody,
    OpenAPI20Parameter,
    OpenAPI30Body,
    OpenAPI30Parameter,
)

DESCRIPTION = "Foo"


@pytest.mark.parametrize(
    "cls, kwargs, expected",
    (
        (
            OpenAPI20Parameter,
            {"definition": {"description": DESCRIPTION, "in": "query", "name": "foo", "type": "string"}},
            DESCRIPTION,
        ),
        (
            OpenAPI30Parameter,
            {"definition": {"description": DESCRIPTION, "in": "query", "name": "foo", "schema": {"type": "string"}}},
            DESCRIPTION,
        ),
        (
            OpenAPI20Body,
            {
                "definition": {"description": DESCRIPTION, "in": "body", "name": "foo", "schema": {"type": "string"}},
                "media_type": "application/json",
            },
            DESCRIPTION,
        ),
        (
            OpenAPI30Body,
            {
                "definition": {"schema": {"type": "string"}},
                "description": DESCRIPTION,
                "media_type": "application/json",
            },
            DESCRIPTION,
        ),
        (
            OpenAPI20CompositeBody,
            {
                "definition": [OpenAPI20Parameter({"in": "formData", "name": "foo", "type": "string"})],
                "media_type": "application/x-www-form-urlencoded",
            },
            None,
        ),
    ),
)
def test_description(cls, kwargs, expected):
    assert cls(**kwargs).description == expected
