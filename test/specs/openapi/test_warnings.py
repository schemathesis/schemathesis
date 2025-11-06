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
    assert warning.message == "Cannot validate response 200: no deserializer registered for application/msgpack"


def test_detect_missing_deserializers_with_custom_media_type(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 1
    assert warnings[0].operation_label == "GET /users"
    assert warnings[0].status_code == "200"
    assert warnings[0].content_type == "application/msgpack"


def test_detect_missing_deserializers_with_json(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_no_schema(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_primitive_type(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_with_registered_deserializer(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 0


def test_detect_missing_deserializers_array_type(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 1
    assert warnings[0].content_type == "application/msgpack"


def test_detect_missing_deserializers_with_malformed_media_type(ctx):
    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    # Should not raise exception and should only warn about the valid media type
    warnings = detect_missing_deserializers(operation)

    assert len(warnings) == 1
    assert warnings[0].content_type == "application/msgpack"
