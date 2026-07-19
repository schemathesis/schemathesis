import pytest

import schemathesis
from schemathesis.config import SchemathesisWarning
from schemathesis.specs.openapi.warnings import MissingDeserializerWarning, detect_missing_deserializers


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
