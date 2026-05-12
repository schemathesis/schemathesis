import jsonschema_rs

from schemathesis.generation import GenerationMode
from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases

# Malformed regex - bad character range `\\-.`
MALFORMED_REGEX = "^[A-Za-z0-9 \\\\-.'À-ÿ]+$"


def _load_json_body_operation(
    ctx,
    body_schema,
    *,
    path="/items",
    method="post",
    version="3.0.2",
    body_required=True,
    parameters=None,
    **kwargs,
):
    request_body = {"content": {"application/json": {"schema": body_schema}}}
    if body_required is not None:
        request_body["required"] = body_required
    operation = {"requestBody": request_body, "responses": {"200": {"description": "OK"}}}
    if parameters is not None:
        operation["parameters"] = parameters
    schema = ctx.openapi.load_schema({path: {method: operation}}, version=version, **kwargs)
    return schema[path][method.upper()]


def _collect_coverage_cases(operation, generation_mode):
    return list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=[generation_mode],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
    )


def _assert_generated_bodies_match_schema(operation, generation_mode, *, validate_formats=False, require_bodies=True):
    body_schema = operation.body[0].optimized_schema
    validator_kwargs = {"validate_formats": True} if validate_formats else {}
    validator = jsonschema_rs.validator_for(body_schema, **validator_kwargs)
    bodies = [case.body for case in _collect_coverage_cases(operation, generation_mode) if case.body is not None]
    if require_bodies:
        assert bodies, f"Expected at least one {generation_mode.name} body case"
    expect_valid = generation_mode == GenerationMode.POSITIVE
    for body in bodies:
        is_valid = validator.is_valid(body)
        if expect_valid:
            assert is_valid, f"{generation_mode.name} body is schema-invalid: {body!r}"
        else:
            assert not is_valid, f"{generation_mode.name} body is schema-valid (false positive): {body!r}"
    return bodies


def test_malformed_regex_removed_allows_body_generation(ctx):
    # When a body schema contains a malformed regex pattern, it is removed during conversion
    # allowing data generation to proceed
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string", "pattern": MALFORMED_REGEX}},
        },
        path="/api/orders/{orderId}",
        method="put",
        version="3.0.2",
        parameters=[
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
    )

    # Cases are generated because the malformed pattern is removed
    cases = _collect_coverage_cases(operation, GenerationMode.POSITIVE)
    assert len(cases) > 0


def test_numeric_pattern_value(ctx):
    # When a body schema contains a pattern with a numeric value instead of a string,
    # it should be handled gracefully without raising a TypeError
    operation = _load_json_body_operation(
        ctx,
        {
            "properties": {
                "key": {
                    "pattern": 0.0  # Invalid: pattern should be a string
                }
            }
        },
        path="/test",
        method="patch",
        version="3.0.0",
        body_required=None,
    )

    # Cases should be generated despite the invalid pattern value
    cases = _collect_coverage_cases(operation, GenerationMode.POSITIVE)
    assert len(cases) > 0


def test_required_property_not_in_properties_is_generated(ctx):
    # When a schema's `required` array names a property that has no entry in
    # `properties`, coverage must still emit a value for that key so the
    # generated body satisfies the `required` constraint and is schema-valid.
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            # `host` is required but has no definition in properties
            "required": ["name", "host"],
            "properties": {
                "name": {"type": "string"},
                "port": {"type": "integer"},
            },
        },
        path="/listeners",
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.POSITIVE)


def test_invalid_enum_values_excluded_from_positive_cases(ctx):
    # When a schema property has `type: string` but the enum contains a non-string value (false),
    # coverage must not emit the invalid enum value in POSITIVE mode.
    # Such values commonly arise from YAML deserialization (e.g. bare `NO` parsed as boolean false).
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "country": {
                    "type": "string",
                    # `false` is an invalid enum value for type:string
                    "enum": ["US", "GB", False],
                }
            },
        },
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.POSITIVE)


def test_invalid_enum_items_excluded_from_positive_array_cases(ctx):
    # When an array property's items schema has `type: string` but the enum contains
    # a non-string value (false), coverage must not emit arrays with the invalid value.
    # Such values commonly arise from YAML deserialization (e.g. bare `NO` parsed as boolean false).
    operation = _load_json_body_operation(
        ctx,
        {
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
        },
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.POSITIVE)


def test_allof_with_outer_properties_includes_required_fields(ctx):
    # When a body schema combines allOf (which declares required fields) with additional outer-level properties
    # Coverage must include the required fields in every generated case
    operation = _load_json_body_operation(
        ctx,
        {
            "allOf": [
                {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                }
            ],
            # outer properties beyond allOf - no explicit type or required
            "properties": {"details": {"properties": {"key": {"type": "string"}}}},
        },
        path="/resources",
        method="put",
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.POSITIVE)


def test_allof_with_explicit_type_object_includes_required_fields(ctx):
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            "allOf": [
                {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                }
            ],
            "properties": {"details": {"properties": {"key": {"type": "string"}}}},
        },
        path="/resources",
        method="put",
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.POSITIVE)


def test_format_invalid_default_not_used_as_const(ctx):
    # When a schema property has format: duration with a default that is NOT a valid
    # ISO 8601 duration (e.g. Azure's "7.00:00:00" instead of "P7D"), the coverage
    # generator must NOT emit the invalid default as a const value.  Doing so produces
    # a body that passes is_valid() (no format validation) but is rejected by the
    # conformance validator which uses validate_formats=True.
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "constraints": {
                    "type": "object",
                    "properties": {
                        "maxWallClockTime": {
                            "type": "string",
                            "format": "duration",
                            # Azure uses "7.00:00:00" - not valid ISO 8601
                            "default": "7.00:00:00",
                        }
                    },
                }
            },
        },
        path="/jobs",
        method="put",
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.POSITIVE, validate_formats=True)


def test_swagger2_array_query_param_with_top_level_enum(ctx):
    # When a Swagger 2.0 array parameter has both top-level `enum` and `items` (a contradictory
    # codegen artifact), coverage must still emit the required parameter with a valid array value.
    schema = ctx.openapi.load_schema(
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
    operation = schema["/collection/purpose"]["PUT"]

    cases = _collect_coverage_cases(operation, GenerationMode.POSITIVE)

    query_cases = [c for c in cases if c.query and "purposes" in c.query]
    assert query_cases, "Expected at least one case with 'purposes' in query"
    for c in query_cases:
        assert isinstance(c.query["purposes"], list), f"Expected list, got: {c.query['purposes']!r}"


def test_minlength_maxlength_negative_skipped_for_integer_type(ctx):
    # When a schema property has type:integer but also specifies minLength/maxLength
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "ttl": {
                    "type": "integer",
                    # minLength/maxLength are string-only constraints;
                    # applying them to an integer field likely is a schema bug
                    "minLength": 30,
                    "maxLength": 3600,
                }
            },
        },
        path="/cache",
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.NEGATIVE, require_bodies=False)


def test_deep_allof_chain_with_inherited_additional_properties_populates_inner_required(ctx):
    # `additionalProperties: false` inherited through a chain of allOf bases would otherwise drop the wrapper's required keys.
    operation = _load_json_body_operation(
        ctx,
        {"$ref": "#/components/schemas/Envelope"},
        path="/items",
        method="put",
        components={
            "schemas": {
                "Base": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"baseField": {"type": "string"}},
                },
                "Intermediate": {
                    "type": "object",
                    "additionalProperties": False,
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                },
                "Wrapper": {
                    "type": "object",
                    "additionalProperties": False,
                    "allOf": [{"$ref": "#/components/schemas/Intermediate"}],
                    "properties": {
                        "first": {"type": "string"},
                        "second": {"type": "string"},
                    },
                    "required": ["first", "second"],
                },
                "Envelope": {
                    "type": "object",
                    "properties": {"payload": {"$ref": "#/components/schemas/Wrapper"}},
                    "required": ["payload"],
                },
            }
        },
    )

    bodies = [
        case.body for case in _collect_coverage_cases(operation, GenerationMode.POSITIVE) if case.body is not None
    ]
    populated = [
        body
        for body in bodies
        if isinstance(body.get("payload"), dict) and {"first", "second"} <= body["payload"].keys()
    ]
    assert populated, f"Expected positive body with `payload.first` and `payload.second`, got {bodies!r}"


def test_required_enforced_when_properties_at_threshold(ctx):
    # When a schema has exactly 15 properties (at the jsonschema_rs SmallProperties threshold)
    # and required lists exactly 2 of them, NEGATIVE cases must still be schema-invalid.
    properties = {f"field{i}": {"type": "string"} for i in range(15)}
    operation = _load_json_body_operation(
        ctx,
        {
            "type": "object",
            "required": ["field0", "field1"],
            "properties": properties,
        },
        path="/things",
    )
    _assert_generated_bodies_match_schema(operation, GenerationMode.NEGATIVE)
