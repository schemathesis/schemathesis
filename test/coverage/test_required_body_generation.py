from schemathesis import GenerationMode
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
