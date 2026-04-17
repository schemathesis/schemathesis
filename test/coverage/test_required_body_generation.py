import jsonschema_rs

import schemathesis
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import _iter_coverage_cases

# Malformed regex - bad character range `\\-.`
MALFORMED_REGEX = "^[A-Za-z0-9 \\\\-.'À-ÿ]+$"


def test_malformed_regex_removed_allows_body_generation(ctx):
    # When a body schema contains a malformed regex pattern, it is removed during conversion
    # allowing data generation to proceed
    schema_dict = ctx.openapi.build_schema(
        {
            "/api/orders/{orderId}": {
                "put": {
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9A-Z]{26}$"},
                        },
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "X-Optional",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string", "pattern": MALFORMED_REGEX}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.2",
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/api/orders/{orderId}"]["PUT"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    # Cases are generated because the malformed pattern is removed
    assert len(cases) > 0


def test_numeric_pattern_value(ctx):
    # When a body schema contains a pattern with a numeric value instead of a string,
    # it should be handled gracefully without raising a TypeError
    schema_dict = ctx.openapi.build_schema(
        {
            "/test": {
                "patch": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "key": {
                                            "pattern": 0.0  # Invalid: pattern should be a string
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/test"]["PATCH"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    # Cases should be generated despite the invalid pattern value
    assert len(cases) > 0


def test_required_property_not_in_properties_is_generated(ctx):
    # When a schema's `required` array names a property that has no entry in
    # `properties`, coverage must still emit a value for that key so the
    # generated body satisfies the `required` constraint and is schema-valid.
    schema_dict = ctx.openapi.build_schema(
        {
            "/listeners": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    # `host` is required but has no definition in properties
                                    "required": ["name", "host"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "port": {"type": "integer"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/listeners"]["POST"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)
    positive_bodies = [c.body for c in cases if c.body is not None]
    assert positive_bodies, "Expected at least one body case"
    for body in positive_bodies:
        assert validator.is_valid(body), f"POSITIVE body is schema-invalid: {body!r}"


def test_invalid_enum_values_excluded_from_positive_cases(ctx):
    # When a schema property has `type: string` but the enum contains a non-string value (false),
    # coverage must not emit the invalid enum value in POSITIVE mode.
    # Such values commonly arise from YAML deserialization (e.g. bare `NO` parsed as boolean false).
    schema_dict = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "country": {
                                            "type": "string",
                                            # `false` is an invalid enum value for type:string
                                            "enum": ["US", "GB", False],
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/items"]["POST"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)
    positive_bodies = [c.body for c in cases if c.body is not None]
    assert positive_bodies, "Expected at least one body case"
    for body in positive_bodies:
        assert validator.is_valid(body), f"POSITIVE body is schema-invalid: {body!r}"


def test_invalid_enum_items_excluded_from_positive_array_cases(ctx):
    # When an array property's items schema has `type: string` but the enum contains
    # a non-string value (false), coverage must not emit arrays with the invalid value.
    # Such values commonly arise from YAML deserialization (e.g. bare `NO` parsed as boolean false).
    schema_dict = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "countries": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                # `false` is an invalid enum value for type:string
                                                "enum": ["US", "GB", False],
                                            },
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/items"]["POST"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)
    positive_bodies = [c.body for c in cases if c.body is not None]
    assert positive_bodies, "Expected at least one body case"
    for body in positive_bodies:
        assert validator.is_valid(body), f"POSITIVE body is schema-invalid: {body!r}"


def test_allof_with_outer_properties_includes_required_fields(ctx):
    # When a body schema combines allOf (which declares required fields) with additional outer-level properties
    # Coverage must include the required fields in every generated case
    schema_dict = ctx.openapi.build_schema(
        {
            "/resources": {
                "put": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "allOf": [
                                        {
                                            "type": "object",
                                            "required": ["name"],
                                            "properties": {"name": {"type": "string"}},
                                        }
                                    ],
                                    # outer properties beyond allOf - no explicit type or required
                                    "properties": {"details": {"properties": {"key": {"type": "string"}}}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/resources"]["PUT"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)
    positive_bodies = [c.body for c in cases if c.body is not None]
    assert positive_bodies, "Expected at least one body case"
    for body in positive_bodies:
        assert validator.is_valid(body), f"POSITIVE body is schema-invalid: {body!r}"


def test_allof_with_explicit_type_object_includes_required_fields(ctx):
    schema_dict = ctx.openapi.build_schema(
        {
            "/resources": {
                "put": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "allOf": [
                                        {
                                            "type": "object",
                                            "required": ["name"],
                                            "properties": {"name": {"type": "string"}},
                                        }
                                    ],
                                    "properties": {"details": {"properties": {"key": {"type": "string"}}}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/resources"]["PUT"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)
    positive_bodies = [c.body for c in cases if c.body is not None]
    assert positive_bodies, "Expected at least one body case"
    for body in positive_bodies:
        assert validator.is_valid(body), f"POSITIVE body is schema-invalid: {body!r}"


def test_swagger2_array_query_param_with_top_level_enum(ctx):
    # When a Swagger 2.0 array parameter has both top-level `enum` and `items` (a contradictory
    # codegen artifact), coverage must still emit the required parameter with a valid array value.
    schema_dict = ctx.openapi.build_schema(
        {
            "/collection/purpose": {
                "put": {
                    "parameters": [
                        {
                            "name": "purposes",
                            "in": "query",
                            "required": True,
                            "type": "array",
                            "collectionFormat": "multi",
                            # enum at array level is a Swagger 2.0 quirk — item-level constraint
                            "enum": ["FEATURES", "LANDMARKS", "ATTRIBUTES"],
                            "items": {
                                "type": "string",
                                "enum": ["FEATURES", "LANDMARKS", "ATTRIBUTES"],
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/collection/purpose"]["PUT"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    query_cases = [c for c in cases if c.query and "purposes" in c.query]
    assert query_cases, "Expected at least one case with 'purposes' in query"
    for c in query_cases:
        assert isinstance(c.query["purposes"], list), f"Expected list, got: {c.query['purposes']!r}"
