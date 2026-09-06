import pytest

import schemathesis
from schemathesis.config import SchemathesisWarning
from schemathesis.specs.openapi.warnings import (
    MissingDeserializerWarning,
    UnresolvableReferenceWarning,
    detect_missing_deserializers,
    detect_unresolvable_references,
)


def test_missing_deserializer_warning_properties():
    warning = MissingDeserializerWarning(
        operation_label="GET /users",
        status_code="200",
        content_type="application/msgpack",
    )

    assert warning.kind == SchemathesisWarning.MISSING_DESERIALIZER
    assert warning.message == "200"
    assert warning.group == "application/msgpack"


def test_detect_missing_deserializers_with_custom_media_type(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 1
    assert warnings[0].operation_label == "GET /users"
    assert warnings[0].status_code == "200"
    assert warnings[0].content_type == "application/msgpack"


def test_detect_missing_deserializers_with_json(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_no_schema(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "204": {
                            "description": "No content",
                        }
                    }
                }
            }
        }
    )
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_primitive_type(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {"schema": {"type": "string"}},
                            },
                        }
                    }
                }
            }
        }
    )
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_with_registered_deserializer(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    @schemathesis.deserializer("application/msgpack")
    def msgpack_deserializer(ctx, response):
        return {}

    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_array_type(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 1
    assert warnings[0].content_type == "application/msgpack"


def test_detect_missing_deserializers_with_malformed_media_type(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                # Malformed media type (missing subtype)
                                "invalid-media-type": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                },
                                # Valid media type without deserializer
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                                },
                            },
                        }
                    }
                }
            }
        }
    )
    operation = schema["/users"]["GET"]

    # Should not raise exception and should only warn about the valid media type
    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 1
    assert warnings[0].content_type == "application/msgpack"


# A `text/html` body typed as a string needs no deserializer, regardless of what sibling media types declare.
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            {
                "application/geo+json": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
                "text/html": {"schema": {"type": "string"}},
            },
            [],
        ),
        (
            {
                "text/html": {"schema": {"type": "string"}},
                "application/msgpack": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
            },
            ["application/msgpack"],
        ),
        (
            {
                "text/html": {"example": "<p>hi</p>"},
                "application/msgpack": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
            },
            ["application/msgpack"],
        ),
    ],
    ids=["unstructured-sibling", "structured-non-first", "schemaless-sibling"],
)
def test_detect_missing_deserializers_judges_each_media_type_separately(ctx, content, expected):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"responses": {"200": {"description": "Success", "content": content}}}}}
    )

    assert detect_missing_deserializers(schema["/users"]["GET"]) == [
        MissingDeserializerWarning(operation_label="GET /users", status_code="200", content_type=content_type)
        for content_type in expected
    ]


MISSING_SCHEMA = {"$ref": "#/components/schemas/Missing"}


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            {
                "parameters": [{"in": "query", "name": "filter", "required": False, "schema": MISSING_SCHEMA}],
                "responses": {"200": {"description": "Success"}},
            },
            [("`query` parameter `filter`", "#/components/schemas/Missing")],
        ),
        (
            {
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                        "headers": {"X-Total": {"schema": MISSING_SCHEMA}},
                    },
                    "404": {
                        "description": "Not Found",
                        "content": {"*/*": {"schema": {"$ref": "#/components/schemas/ExceptionResponse"}}},
                    },
                }
            },
            [
                ("response `200` header `X-Total`", "#/components/schemas/Missing"),
                ("response `404`", "#/components/schemas/ExceptionResponse"),
            ],
        ),
        (
            {
                "responses": {
                    "200": {"description": "Success", "headers": {"X-Total": {"$ref": "#/components/headers/Missing"}}}
                }
            },
            [("response `200` header `X-Total`", "#/components/headers/Missing")],
        ),
        (
            {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}},
                },
                "responses": {"200": {"description": "Success"}},
            },
            [("`body`", "#/components/schemas/Missing")],
        ),
        ({"responses": {"200": {"description": "Success", "content": {"application/json": "not-an-object"}}}}, []),
        (
            {
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {"application/json": {"examples": {"first": {"value": {}}}}},
                    }
                }
            },
            [],
        ),
    ],
    ids=[
        "optional-parameter",
        "response-schema-and-header",
        "missing-header-definition",
        "dropped-required-body",
        "media-type-not-an-object",
        "media-type-without-schema",
    ],
)
def test_detect_unresolvable_references(ctx, operation, expected):
    schema = ctx.openapi.load_schema({"/users": {"get": operation}})

    assert detect_unresolvable_references(schema["/users"]["GET"]) == [
        UnresolvableReferenceWarning(operation_label="GET /users", subject=subject, reference=reference)
        for subject, reference in expected
    ]
