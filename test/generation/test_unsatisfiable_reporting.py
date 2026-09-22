import pytest

from schemathesis.generation.hypothesis.reporting import (
    _describe_keyword,
    build_unsatisfiable_error,
    describe_unsatisfiable,
)

RESPONSES = {"responses": {"200": {"description": "OK"}}}
DEAD_STRING = {"type": "string", "minLength": 5, "maxLength": 2}


def load_body(ctx, schema, components=None, version="3.0.2"):
    return ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": schema}}},
                    **RESPONSES,
                }
            }
        },
        components={"schemas": components or {}},
        version=version,
    )


def body_message(ctx, schema, components=None):
    return str(build_unsatisfiable_error(load_body(ctx, schema, components)["/test"]["POST"], with_tip=False))


def body_detail(ctx, schema, components=None, version="3.0.2"):
    operation = load_body(ctx, schema, components, version)["/test"]["POST"]
    parameter = next(iter(operation.body))
    return describe_unsatisfiable(
        parameter.optimized_schema, parameter.name_to_uri, operation.schema.adapter.jsonschema_validator_cls
    )


def required_chain(depth):
    node = DEAD_STRING
    for _ in range(depth):
        node = {"type": "object", "required": ["a"], "properties": {"a": node}}
    return node


def test_message_names_the_keywords_with_their_values(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 100, "maximum": 10},
                        }
                    ],
                    **RESPONSES,
                }
            }
        }
    )

    assert (
        str(build_unsatisfiable_error(schema["/test"]["GET"], with_tip=False))
        == """Cannot generate test data for query parameter 'id'
Schema:

{
    "type": "integer",
    "minimum": 100,
    "maximum": 10
}

Nothing satisfies `minimum: 100` and `maximum: 10`"""
    )


@pytest.mark.parametrize(
    ("body_schema", "components", "expected"),
    [
        (
            {"type": "object", "required": ["name"], "properties": {"name": DEAD_STRING}},
            None,
            '`type: "string"` conflicts with `minLength: 5` and `maxLength: 2` at /properties/name',
        ),
        (
            {"$ref": "#/components/schemas/Wrapper"},
            {
                "Number": {"type": "integer"},
                "Text": {"type": "string"},
                "Wrapper": {"allOf": [{"$ref": "#/components/schemas/Number"}, {"$ref": "#/components/schemas/Text"}]},
            },
            '`type: "integer"` at /components/schemas/Number'
            ' conflicts with `type: "string"` at /components/schemas/Text',
        ),
        (
            {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 10, "maximum": 1}},
            None,
            "Nothing satisfies `minimum: 10` and `maximum: 1` at /items",
        ),
        ({"enum": []}, None, "Nothing satisfies `enum: []`"),
    ],
    ids=["a dead required property", "branches behind references", "an element no value fits", "an empty enum"],
)
def test_message_names_the_subschema_that_carries_the_conflict(ctx, body_schema, components, expected):
    assert body_message(ctx, body_schema, components).endswith(f"\n\n{expected}")


@pytest.mark.parametrize(
    "body_schema",
    [
        {"type": "object", "properties": {"name": DEAD_STRING}},
        {"type": "object", "additionalProperties": False},
        {"type": "array", "items": False},
        {"type": "string", "pattern": "^[A-Z]{5,}$", "maxLength": 2},
    ],
    ids=["an optional dead property", "a closed object", "an array that takes no element", "an undecided pattern"],
)
def test_emptiness_that_does_not_reach_the_root_is_not_reported(ctx, body_schema):
    # A `false` under `additionalProperties` is an empty position, not a broken schema.
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}},
                    **RESPONSES,
                }
            }
        }
    )
    parameter = next(iter(schema["/test"]["POST"].body))

    assert (
        describe_unsatisfiable(
            parameter.optimized_schema, parameter.name_to_uri, schema.adapter.jsonschema_validator_cls
        )
        is None
    )


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "integer", "minimum": 10, "oneOf": [{"maximum": 1}, {"maximum": 2}]},
            "`maximum: 1` at /oneOf/0 and `maximum: 2` at /oneOf/1 conflict with `minimum: 10`",
        ),
        (
            {"type": "integer", "minimum": 10, "anyOf": [{"maximum": 1}, {"maximum": 2}]},
            "`maximum: 1` at /anyOf/0 and `maximum: 2` at /anyOf/1 conflict with `minimum: 10`",
        ),
        (
            {"type": "integer", "minimum": 10, "oneOf": [{"maximum": index} for index in range(6)]},
            "`maximum: 0` at /oneOf/0, `maximum: 1` at /oneOf/1, `maximum: 2` at /oneOf/2"
            " and 3 more branches conflict with `minimum: 10`",
        ),
        (
            {
                "type": "object",
                "required": ["a"],
                "properties": {"a": {"type": "integer"}},
                "oneOf": [{"properties": {"a": {"type": "string"}}}, {"properties": {"a": {"type": "boolean"}}}],
            },
            "the branch at /oneOf/0 and the branch at /oneOf/1 conflict with"
            ' `properties: {"a": {"type": "integer"}}` and `required: ["a"]`',
        ),
        (
            {"type": "integer", "minimum": 10, "oneOf": [{"maximum": 1}]},
            "`maximum: 1` at /oneOf/0 conflicts with `minimum: 10`",
        ),
        (
            {
                "type": "string",
                "minLength": 3,
                "oneOf": [{"minLength": 5, "maxLength": 2}, {"minLength": 6, "maxLength": 3}],
            },
            "Nothing satisfies `minLength: 5` and `maxLength: 2` at /oneOf/0"
            " and `minLength: 6` and `maxLength: 3` at /oneOf/1",
        ),
    ],
    ids=[
        "oneOf",
        "anyOf",
        "more branches than fit",
        "branches whose own keywords survive the merge",
        "a single branch",
        "branches empty on their own",
    ],
)
def test_message_names_the_branches_their_siblings_leave_empty(ctx, schema, expected):
    assert body_detail(ctx, schema) == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "integer", "minimum": 10, "maximum": 1, "oneOf": [{"maximum": 1}, {"minimum": 20}]},
            "Nothing satisfies `minimum: 10` and `maximum: 1`",
        ),
        ({"type": "integer", "oneOf": [{"minimum": 1}, {"minimum": 1}]}, "Nothing satisfies `oneOf`"),
    ],
    ids=["the conflict is elsewhere", "every value matches more than one branch"],
)
def test_branches_are_not_named_when_they_admit_values(ctx, schema, expected):
    assert body_detail(ctx, schema) == expected


def test_branches_are_checked_against_siblings_behind_a_reference(ctx):
    detail = body_detail(
        ctx,
        {"type": "integer", "allOf": [{"$ref": "#/components/schemas/Min"}], "oneOf": [{"maximum": 1}, {"maximum": 2}]},
        components={"Min": {"minimum": 10}},
    )

    assert detail == (
        "`minimum: 10` at /components/schemas/Min conflicts with the branch at /oneOf/0 and the branch at /oneOf/1"
    )


@pytest.mark.parametrize(
    ("schema", "version", "expected"),
    [
        (
            {"type": "string", "enum": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]},
            "3.0.2",
            'Nothing satisfies `type: "string"` and `enum`',
        ),
        (
            {"allOf": [{"allOf": [{"type": "string"}]}, {"type": "integer"}]},
            "3.0.2",
            'the schema at /allOf/0 conflicts with `type: "integer"` at /allOf/1',
        ),
        (
            {"type": "object", "required": ["a"], "properties": {"a": False}},
            "3.1.0",
            "The schema is `false` at /properties/a",
        ),
        (
            {"type": "integer", "minimum": 1, "oneOf": [False, False]},
            "3.1.0",
            "Nothing satisfies the branch at /oneOf/0 and the branch at /oneOf/1",
        ),
    ],
    ids=[
        "a value too long to print",
        "a branch with no keywords of its own",
        "a subschema written as false",
        "branches written as false",
    ],
)
def test_message_falls_back_to_naming_the_location(ctx, schema, version, expected):
    assert body_detail(ctx, schema, version=version) == expected


def test_blame_stops_following_a_chain_that_never_ends(ctx):
    # Ten hops in, the pointer names where the search got to rather than the leaf.
    assert body_detail(ctx, required_chain(12)) == (
        '`type: "object"` conflicts with `properties` and `required: ["a"]` at ' + "/properties/a" * 10
    )


@pytest.mark.parametrize(
    ("schema", "keyword", "expected"),
    [
        ({"minimum": 10}, "minimum", "`minimum: 10`"),
        ({"$ref": "#/x-bundled/schema1"}, "type", "`type`"),
        ({"enum": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]}, "enum", "`enum`"),
    ],
    ids=["a short value", "a keyword the node does not hold", "a value too long to print"],
)
def test_keyword_is_named_with_its_value_when_it_reads(schema, keyword, expected):
    assert _describe_keyword(schema, "", keyword) == expected


def test_message_keeps_the_generic_causes_when_nothing_is_proven_empty(ctx):
    # The pattern and the length bound do collide, but the canonical form does not decide it.
    assert body_message(ctx, {"type": "string", "pattern": "^[A-Z]{5,}$", "maxLength": 2}).endswith(
        """This usually means:
  - Type mismatch (e.g., enum with strings but type: integer)
  - Contradictory constraints (e.g., minimum > maximum)
  - Regex that's too complex to generate values for"""
    )
