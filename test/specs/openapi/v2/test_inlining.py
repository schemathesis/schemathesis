import jsonschema
import pytest

from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._jsonschema.inlining import on_reached_limit
from schemathesis.specs.openapi._jsonschema.errors import InfiniteRecursionError

RECURSIVE_REFERENCE = {"$ref": "#/definitions/Person"}
RECURSIVE = set(RECURSIVE_REFERENCE.values())


def can_validate(request):
    # Some recursive schemas recurse infinitely in validation, therefore we can't validate them
    return request.node.callspec.id not in (
        "self-ref",
        "anyOf-0th",
        "anyOf-1st",
        "allOf-nested",
    )


# TODO: Run this on some corpus


def get_by_path(schema, path):
    for part in path:
        schema = schema[part]
    return schema


@pytest.mark.parametrize(
    "schema, same_objects",
    [
        (RECURSIVE_REFERENCE, []),
        ({"$ref": "#/definitions/Non-Recursive"}, []),
        (
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "key": True,
                    "friend": RECURSIVE_REFERENCE,
                },
            },
            [["properties", "name"]],
        ),
        (
            {
                "type": "array",
                "items": {"type": "object"},
            },
            [[]],
        ),
        (
            {
                "type": "array",
                "items": True,
            },
            [[]],
        ),
        (
            {
                "type": "array",
                "items": RECURSIVE_REFERENCE,
            },
            [],
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": {"type": "object"},
            },
            [[]],
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": RECURSIVE_REFERENCE,
            },
            [["properties", "name"]],
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": RECURSIVE_REFERENCE,
                "minProperties": 1,
            },
            [["properties", "name"]],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "friend": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "friend": RECURSIVE_REFERENCE,
                        },
                    },
                },
            },
            [
                ["properties", "name"],
                ["properties", "friend", "properties", "name"],
            ],
        ),
        (
            {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": RECURSIVE_REFERENCE,
                },
            },
            [],
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": RECURSIVE_REFERENCE,
                },
            },
            [["properties", "name"], ["additionalProperties", "properties"]],
        ),
        (
            {
                "type": "array",
                "items": [
                    RECURSIVE_REFERENCE,
                ],
            },
            [],
        ),
        (
            {
                "type": "array",
                "items": [
                    {"type": "string"},
                    RECURSIVE_REFERENCE,
                ],
            },
            [["items", 0]],
        ),
        (
            {
                "type": "array",
                "items": [
                    {"type": "string"},
                    True,
                ],
            },
            [[]],
        ),
        (
            {
                "anyOf": [
                    {"type": "object"},
                ],
            },
            [[]],
        ),
        (
            {
                "anyOf": [
                    RECURSIVE_REFERENCE,
                    {"type": "object"},
                ],
            },
            [
                [
                    ("anyOf", 1),
                    ("anyOf", 0),
                ]
            ],
        ),
        (
            {
                "anyOf": [
                    {"type": "object"},
                    RECURSIVE_REFERENCE,
                    True,
                ],
            },
            [["anyOf", 0]],
        ),
        (
            {
                "anyOf": [
                    {"type": "object"},
                    {
                        "anyOf": [
                            RECURSIVE_REFERENCE,
                            {"type": "object"},
                        ],
                    },
                ],
            },
            [["anyOf", 0], [("anyOf", 1, "anyOf", 1), ("anyOf", 1, "anyOf", 0)]],
        ),
        (
            {
                "allOf": [
                    {"type": "object"},
                    {
                        "anyOf": [
                            RECURSIVE_REFERENCE,
                            {"type": "object"},
                        ],
                    },
                ],
            },
            [["allOf", 0], [("allOf", 1, "anyOf", 1), ("allOf", 1, "anyOf", 0)]],
        ),
        (
            {
                "allOf": [
                    {"type": "object"},
                    True,
                ],
            },
            [],
        ),
        (
            {"not": {"type": "object"}},
            [[]],
        ),
        (
            {
                "not": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": RECURSIVE_REFERENCE,
                    },
                }
            },
            [],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "friend": {
                        "type": "object",
                        "properties": {
                            "nested": RECURSIVE_REFERENCE,
                        },
                        "required": ["nested"],
                    }
                },
            },
            [],
        ),
        (
            {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": {
                        "properties": {
                            "friend": RECURSIVE_REFERENCE,
                        },
                        "required": ["friend"],
                    },
                },
            },
            [],
        ),
        (
            {
                "type": "array",
                "items": {
                    "properties": {
                        "friend": RECURSIVE_REFERENCE,
                    },
                    "required": ["friend"],
                },
            },
            [],
        ),
        (
            {
                "anyOf": [
                    {"type": "object"},
                    {
                        "properties": {
                            "friend": RECURSIVE_REFERENCE,
                        },
                        "required": ["friend"],
                    },
                    True,
                ],
            },
            [],
        ),
    ],
    ids=[
        "self-ref",
        "non-recursive-ref",
        "properties",
        "items-no-change",
        "items-no-change-bool",
        "items",
        "additional-properties",
        "additional-properties-no-change",
        "additional-properties-with-min-items-valid",
        "properties-nested",
        "items-nested",
        "additional-properties-nested",
        "items-array-0th",
        "items-array-1st",
        "items-array-bool",
        "anyOf-no-change",
        "anyOf-0th",
        "anyOf-1st",
        "anyOf-nested",
        "allOf-nested",
        "allOf-no-change",
        "not-no-change",
        "not-nested",
        "non-removable-in-removable-properties",
        "non-removable-in-removable-additional-properties",
        "non-removable-in-removable-items",
        "non-removable-in-removable-anyOf",
    ],
)
def test_on_reached_limit(request, schema, same_objects, snapshot_json, assert_generates):
    original = schema
    schema = fast_deepcopy(schema)
    unrecursed = on_reached_limit(schema, RECURSIVE)
    assert unrecursed == snapshot_json
    for path in same_objects:
        if path and isinstance(path[0], tuple):
            lhs_path, rhs_path = path
        else:
            lhs_path = rhs_path = path
        # Objects should be reused
        assert get_by_path(schema, lhs_path) is get_by_path(unrecursed, rhs_path)
    assert schema == original

    full_schema = fast_deepcopy(original)
    full_schema["definitions"] = {"Person": original, "Non-Recursive": {"type": "object"}}
    unrecursed["definitions"] = full_schema["definitions"]
    validator = jsonschema.Draft7Validator(full_schema)
    if can_validate(request):

        def check(value):
            validator.validate(instance=value)

    else:
        check = None

    assert_generates(unrecursed, check=check, max_examples=10)


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "friend": RECURSIVE_REFERENCE,
            },
            "required": ["friend"],
        },
        {
            "type": "object",
            "properties": {
                "friend": {
                    "type": "object",
                    "properties": {
                        "nested": RECURSIVE_REFERENCE,
                    },
                    "required": ["nested"],
                }
            },
            "required": ["friend"],
        },
        {
            "anyOf": [RECURSIVE_REFERENCE],
        },
        {
            "minProperties": 1,
            "additionalProperties": RECURSIVE_REFERENCE,
        },
        {
            "minItems": 1,
            "items": RECURSIVE_REFERENCE,
        },
        {
            "minItems": 1,
            "items": {
                "minItems": 1,
                "items": RECURSIVE_REFERENCE,
            },
        },
        {
            "minItems": 1,
            "items": [
                RECURSIVE_REFERENCE,
            ],
        },
        {
            "allOf": [
                RECURSIVE_REFERENCE,
            ],
        },
        {
            "allOf": [
                {
                    "allOf": [
                        RECURSIVE_REFERENCE,
                    ]
                }
            ],
        },
        {
            "not": RECURSIVE_REFERENCE,
        },
    ],
    ids=[
        "properties",
        "properties-nested",
        "anyOf-single-item",
        "additional-properties-min-properties",
        "items-with-object-min-items",
        "items-with-object-nested",
        "items-with-array-nested",
        "allOf",
        "allOf-nested",
        "not",
    ],
)
def test_on_reached_limit_non_removable(schema):
    with pytest.raises(InfiniteRecursionError):
        on_reached_limit(schema, RECURSIVE)
