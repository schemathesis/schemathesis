from schemathesis.generation import GenerationMode
from test.coverage.helpers import assert_bodies, body_operation, iter_cases, load_schema

# Malformed regex - bad character range `\\-.`
MALFORMED_REGEX = "^[A-Za-z0-9 \\\\-.'À-ÿ]+$"


def test_malformed_regex_removed_allows_body_generation(ctx):
    # When a body schema contains a malformed regex pattern, it is removed during conversion
    # allowing data generation to proceed
    operation = body_operation(
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
    assert iter_cases(operation, GenerationMode.POSITIVE)


def test_numeric_pattern_value(ctx):
    # When a body schema contains a pattern with a numeric value instead of a string,
    # it should be handled gracefully without raising a TypeError
    operation = body_operation(
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
    assert iter_cases(operation, GenerationMode.POSITIVE)


def test_required_property_not_in_properties_is_generated(ctx):
    # When a schema's `required` array names a property that has no entry in
    # `properties`, coverage must still emit a value for that key so the
    # generated body satisfies the `required` constraint and is schema-valid.
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_invalid_enum_values_excluded_from_positive_cases(ctx):
    # When a schema property has `type: string` but the enum contains a non-string value (false),
    # coverage must not emit the invalid enum value in POSITIVE mode.
    # Such values commonly arise from YAML deserialization (e.g. bare `NO` parsed as boolean false).
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_invalid_enum_items_excluded_from_positive_array_cases(ctx):
    # When an array property's items schema has `type: string` but the enum contains
    # a non-string value (false), coverage must not emit arrays with the invalid value.
    # Such values commonly arise from YAML deserialization (e.g. bare `NO` parsed as boolean false).
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_allof_with_outer_properties_includes_required_fields(ctx):
    # When a body schema combines allOf (which declares required fields) with additional outer-level properties
    # Coverage must include the required fields in every generated case
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_allof_with_explicit_type_object_includes_required_fields(ctx):
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_format_invalid_default_not_used_as_const(ctx):
    # When a schema property has format: duration with a default that is NOT a valid
    # ISO 8601 duration (e.g. Azure's "7.00:00:00" instead of "P7D"), the coverage
    # generator must NOT emit the invalid default as a const value.  Doing so produces
    # a body that passes is_valid() (no format validation) but is rejected by the
    # conformance validator which uses validate_formats=True.
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_swagger2_array_query_param_with_top_level_enum(ctx):
    # When a Swagger 2.0 array parameter has both top-level `enum` and `items` (a contradictory
    # codegen artifact), coverage must still emit the required parameter with a valid array value.
    operation = load_schema(
        ctx,
        parameters=[
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
        path="/collection/purpose",
        method="put",
        version="2.0",
    )["/collection/purpose"]["put"]

    cases = iter_cases(operation, GenerationMode.POSITIVE)

    query_cases = [c for c in cases if c.query and "purposes" in c.query]
    assert query_cases, "Expected at least one case with 'purposes' in query"
    for c in query_cases:
        assert isinstance(c.query["purposes"], list), f"Expected list, got: {c.query['purposes']!r}"


def test_swagger2_array_query_param_top_level_enum_constrains_items(ctx):
    # Swagger 2.0 idiom: parameter-level `enum` on a `type: array` parameter constrains items.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "status",
                "in": "query",
                "required": True,
                "type": "array",
                "collectionFormat": "multi",
                "enum": ["Active", "Pending", "Closed"],
                "items": {"type": "string"},
            }
        ],
        path="/listings",
        method="get",
        version="2.0",
    )["/listings"]["get"]
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    items_seen: set[str] = set()
    for case in cases:
        value = case.query.get("status") if isinstance(case.query, dict) else None
        if isinstance(value, list):
            items_seen.update(item for item in value if isinstance(item, str))
    assert items_seen == {"Active", "Pending", "Closed"}, f"Expected each enum value covered, got {items_seen!r}"


def test_positive_array_with_maxitems_zero(ctx):
    # `maxItems: 0` permits only `[]`; the items-baseline path must not synthesize a non-empty array.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "stacks": {
                    "type": "array",
                    "maxItems": 0,
                    "items": {"type": "string", "enum": ["unknown"]},
                },
            },
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_positive_not_flip_validates_against_outer_constraints(ctx):
    # The `not` flip yields values that satisfy `not` but may violate other outer constraints (e.g. property types).
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "image_url": {"type": "string"},
                "file_id": {"type": "string"},
            },
            "anyOf": [{"required": ["image_url"]}, {"required": ["file_id"]}],
            "not": {"required": ["image_url", "file_id"]},
            "additionalProperties": False,
        },
        version="3.1.0",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_minlength_maxlength_negative_skipped_for_integer_type(ctx):
    # When a schema property has type:integer but also specifies minLength/maxLength
    operation = body_operation(
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
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


def test_required_enforced_when_properties_at_threshold(ctx):
    # When a schema has exactly 15 properties (at the jsonschema_rs SmallProperties threshold)
    # and required lists exactly 2 of them, NEGATIVE cases must still be schema-invalid.
    properties = {f"field{i}": {"type": "string"} for i in range(15)}
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["field0", "field1"],
            "properties": properties,
        },
        path="/things",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


def test_optional_unsatisfiable_property_does_not_block_siblings(ctx):
    # One optional property with mutually-exclusive `type` + `enum` (a spec bug) must not
    # suppress coverage for the sibling properties.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "good": {"type": "string"},
                "broken": {"type": "number", "enum": ["1", "2"]},
                "choice": {"type": "string", "enum": ["a", "b"]},
            },
        },
    )
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    assert cases, "Expected positive cases despite the unsatisfiable optional"
    populated_choice = {c.body["choice"] for c in cases if isinstance(c.body, dict) and "choice" in c.body}
    assert populated_choice == {"a", "b"}, f"Expected each enum value covered, got {populated_choice!r}"


def test_optional_nullable_emits_null_when_template_omits_it(ctx):
    # When the template omits an optional, the sweep used to dedup the legitimate null
    # emission against an implicit `None`. `deprecated` is one of several root keywords
    # (also `title`, `readOnly`, unknown extensions) that make the template skip optionals.
    operation = body_operation(
        ctx,
        {
            "deprecated": False,
            "type": "object",
            "required": ["req"],
            "properties": {
                "req": {"type": "string", "nullable": True},
                "opt": {"type": "string", "nullable": True},
            },
        },
    )
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    opt_values = [c.body["opt"] for c in cases if isinstance(c.body, dict) and "opt" in c.body]
    assert None in opt_values
    assert any(isinstance(v, str) for v in opt_values)


def test_enum_in_allof_base_with_sibling_ref_property_covers_every_value(ctx):
    # `allOf:[base]` + sibling `properties` with a `$ref` (common in Azure specs).
    # The bundled-ref short-circuit used to skip canonical allOf merging, dropping every
    # enum value reachable only through the base.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Outer"},
        components={
            "schemas": {
                "Base": {
                    "type": "object",
                    "properties": {"storageType": {"type": "string", "enum": ["A", "B"]}},
                },
                "Source": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "PublishingProfile": {
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "type": "object",
                    "properties": {"source": {"$ref": "#/components/schemas/Source"}},
                    "required": ["source"],
                },
                "Outer": {
                    "type": "object",
                    "properties": {"pubProfile": {"$ref": "#/components/schemas/PublishingProfile"}},
                    "required": ["pubProfile"],
                },
            }
        },
    )
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    seen = set()
    for case in cases:
        body = case.body
        if not isinstance(body, dict):
            continue
        pub = body.get("pubProfile")
        if isinstance(pub, dict) and isinstance(pub.get("storageType"), str):
            seen.add(pub["storageType"])
    assert seen == {"A", "B"}, f"Expected both enum values covered, got {seen!r}"


def test_single_branch_allof_keeps_outer_additional_properties(ctx):
    # `additionalProperties: false` beside `allOf` judges the branch's own property names too.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": False,
            "allOf": [{"$ref": "#/components/schemas/Inner"}],
        },
        components={
            "schemas": {
                "Inner": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                }
            }
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)
