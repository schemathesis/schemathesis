import jsonschema
import pytest

from schemathesis.internal.copy import fast_deepcopy
from schemathesis.internal.result import Err
from schemathesis.specs.openapi._jsonschema.inlining import LeafTransformer, unrecurse, UnrecurseContext
from schemathesis.specs.openapi._jsonschema.cache import TransformCache

RECURSIVE_REFERENCE = {"$ref": "#/definitions/Person"}
RECURSIVE_NESTED_REFERENCE = {"$ref": "#/definitions/NestedPerson"}
RECURSIVE_NESTED = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "parent": {"$ref": "#/definitions/NestedPerson"},
    },
}
RECURSIVE_NESTED_UNREMOVABLE_REFERENCE = {"$ref": "#/definitions/UnremovableNestedPerson"}
RECURSIVE_NESTED_UNREMOVABLE = {
    "type": "object",
    "properties": {
        "first": {
            "type": "object",
            "properties": {
                "second": RECURSIVE_NESTED_UNREMOVABLE_REFERENCE,
            },
            "required": ["second"],
        },
    },
    "required": ["first"],
}
RECURSIVE_NESTED_UNREMOVABLE_CYCLE_A_REFERENCE = {"$ref": "#/definitions/UnremovableNestedPerson-Cycle-A"}
RECURSIVE_NESTED_UNREMOVABLE_CYCLE_B_REFERENCE = {"$ref": "#/definitions/UnremovableNestedPerson-Cycle-B"}
RECURSIVE_NESTED_UNREMOVABLE_CYCLE_A = {
    "type": "object",
    "properties": {
        "first": {
            "type": "object",
            "properties": {
                "second": RECURSIVE_NESTED_UNREMOVABLE_CYCLE_B_REFERENCE,
            },
            "required": ["second"],
        },
    },
    "required": ["first"],
}
RECURSIVE_NESTED_UNREMOVABLE_CYCLE_B = {
    "type": "object",
    "properties": {
        "first": {
            "type": "object",
            "properties": {
                "second": RECURSIVE_NESTED_UNREMOVABLE_CYCLE_A_REFERENCE,
            },
        },
    },
    "required": ["first"],
}
RECURSIVE = set(RECURSIVE_REFERENCE.values())


def can_validate(request):
    # Some recursive schemas recurse infinitely in validation, therefore we can't validate them
    return request.node.callspec.id not in (
        "self-ref",
        "anyOf-0th",
        "anyOf-1st",
        "allOf-nested",
    )


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
                "type": "object",
                "properties": {
                    "friend": RECURSIVE_REFERENCE,
                },
            },
            [],
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
                "contains": {
                    "type": "object",
                    "properties": {"friend": RECURSIVE_REFERENCE},
                },
            },
            [],
        ),
        (
            {
                "type": "object",
                "patternProperties": {"^x-": {"type": "object"}},
            },
            [[]],
        ),
        (
            {
                "type": "object",
                "patternProperties": {"^x-": RECURSIVE_REFERENCE},
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
        (
            {
                "type": "object",
                "patternProperties": {
                    "^x-": {
                        "properties": {
                            "friend": RECURSIVE_REFERENCE,
                        },
                        "required": ["friend"],
                    }
                },
            },
            [],
        ),
        (
            {
                "type": "object",
                "patternProperties": {
                    "^t-": True,
                    "^y-": {"type": "integer"},
                    "^x-": {
                        "type": "object",
                        "properties": {
                            "friend": RECURSIVE_REFERENCE,
                        },
                    },
                },
            },
            [["patternProperties", "^y-"]],
        ),
        (
            {
                "propertyNames": {"type": "string"},
            },
            [[]],
        ),
        (
            {
                "propertyNames": RECURSIVE_REFERENCE,
            },
            [],
        ),
        (
            {
                "propertyNames": {
                    "not": RECURSIVE_REFERENCE,
                },
            },
            [],
        ),
        (
            {
                "propertyNames": {
                    "anyOf": [
                        RECURSIVE_REFERENCE,
                        {"type": "string"},
                    ]
                },
            },
            [],
        ),
    ],
    ids=[
        "self-ref",
        "non-recursive-ref",
        "properties",
        "properties-one-prop",
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
        "contains-nested",
        "pattern-properties-no-change",
        "pattern-properties-one-prop",
        "non-removable-in-removable-properties",
        "non-removable-in-removable-additional-properties",
        "non-removable-in-removable-items",
        "non-removable-in-removable-anyOf",
        "non-removable-in-removable-pattern-properties",
        "removable-in-removable-pattern-properties",
        "property-names-no-change",
        "property-names",
        "property-names-nested",
        "property-names-with-modification",
    ],
)
def test_on_reached_limit(request, schema, same_objects, snapshot_json, assert_generates):
    original = schema
    schema = fast_deepcopy(schema)
    unrecursed = LeafTransformer.run(
        schema, UnrecurseContext.new({}, TransformCache(recursive_references=RECURSIVE))
    ).ok()
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
            "type": "object",
            "additionalProperties": {
                "properties": {
                    "friend": RECURSIVE_REFERENCE,
                },
                "required": ["friend"],
            },
            "minProperties": 1,
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
        {
            "not": {"not": RECURSIVE_REFERENCE},
        },
        {
            "not": {
                "anyOf": [
                    RECURSIVE_REFERENCE,
                    {"type": "object"},
                ],
            },
        },
        {
            "patternProperties": {"^x": RECURSIVE_REFERENCE},
            "required": ["x-foo"],
        },
        {
            "patternProperties": {
                "^x": {
                    "type": "object",
                    "anyOf": [RECURSIVE_REFERENCE],
                },
            },
            "required": ["x-foo"],
        },
        {
            "propertyNames": RECURSIVE_REFERENCE,
            "minProperties": 1,
        },
        {
            "propertyNames": {
                "anyOf": [
                    RECURSIVE_REFERENCE,
                ]
            },
            "minProperties": 1,
        },
    ],
    ids=[
        "properties",
        "properties-nested",
        "anyOf-single-item",
        "additional-properties-min-properties",
        "additional-properties-nested-min-properties",
        "items-with-object-min-items",
        "items-with-object-nested",
        "items-with-array-nested",
        "allOf",
        "allOf-nested",
        "not",
        "not-nested",
        "not-with-modification",
        "patternProperties-with-required",
        "patternProperties-nested-with-required",
        "property-names-with-min-properties",
        "property-names-nested-with-min-properties",
    ],
)
def test_on_reached_limit_non_removable(schema):
    result = LeafTransformer.run(schema, UnrecurseContext.new({}, TransformCache(recursive_references=RECURSIVE)))
    assert isinstance(result, Err)


@pytest.mark.parametrize(
    "schema",
    (
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "foo": True,
            },
        },
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "friend": RECURSIVE_NESTED_REFERENCE,
            },
        },
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "first": RECURSIVE_NESTED_REFERENCE,
                "second": RECURSIVE_NESTED_REFERENCE,
            },
        },
        {
            "anyOf": [
                {"type": "object"},
                True,
            ],
        },
        {
            "anyOf": [
                {"type": "object"},
                {
                    "properties": {
                        "name": {"type": "string"},
                        "friend": RECURSIVE_NESTED_REFERENCE,
                    },
                },
            ]
        },
        {
            "anyOf": [
                {"type": "object"},
                {
                    "anyOf": [
                        {"$ref": "#/definitions/Non-Recursive"},
                        {"type": "object"},
                    ],
                },
            ]
        },
        {
            "anyOf": [
                {"type": "object"},
                RECURSIVE_NESTED_REFERENCE,
                RECURSIVE_NESTED_REFERENCE,
            ]
        },
        {
            "anyOf": [
                {"type": "object"},
                {"$ref": "#/definitions/A"},
                {"$ref": "#/definitions/A"},
            ]
        },
        {
            "additionalProperties": {
                "type": "object",
            }
        },
        {
            "additionalProperties": RECURSIVE_NESTED_REFERENCE,
        },
        {
            "items": True,
        },
        {
            "items": {"type": "object"},
        },
        {
            "items": [{"type": "object"}],
        },
        {
            "items": RECURSIVE_NESTED_REFERENCE,
        },
        {
            "items": [RECURSIVE_NESTED_REFERENCE],
        },
        {
            "patternProperties": {
                "^x": {"type": "object"},
            }
        },
        {
            "patternProperties": {
                "^x": RECURSIVE_NESTED_REFERENCE,
            }
        },
        {
            "patternProperties": {
                "^x": RECURSIVE_NESTED_REFERENCE,
                "^y": RECURSIVE_NESTED_REFERENCE,
            }
        },
        {
            "properties": {
                "first": RECURSIVE_NESTED_UNREMOVABLE_REFERENCE,
                "second": {"type": "string"},
            },
        },
        {
            "properties": {
                "first": {
                    "items": RECURSIVE_NESTED_UNREMOVABLE_REFERENCE,
                },
                "second": {"type": "string"},
            }
        },
        {
            "properties": {
                "first": {
                    "items": RECURSIVE_NESTED_UNREMOVABLE_REFERENCE,
                },
                "second": {
                    "items": RECURSIVE_NESTED_UNREMOVABLE_REFERENCE,
                },
                "third": {"type": "string"},
            }
        },
        {
            "properties": {
                "first": {
                    "items": RECURSIVE_NESTED_UNREMOVABLE_CYCLE_A_REFERENCE,
                },
                "third": {"type": "string"},
            }
        },
    ),
    ids=[
        "properties-no-change",
        "properties-direct",
        "properties-multiple-recursive-refs",
        "any-of-no-change",
        "any-of-nested-recursive",
        "any-of-non-recursive",
        "any-of-multiple-recursive-refs",
        "any-of-mutual-recursion",
        "additional-properties-no-change",
        "additional-properties-direct",
        "items-bool",
        "items-object-no-change",
        "items-array-no-change",
        "items-object-direct",
        "items-array-direct",
        "pattern-properties-no-change",
        "pattern-properties-direct",
        "pattern-properties-multiple-recursive-refs",
        "unremovable-behind-removable",
        "unremovable-in-schema",
        "unremovable-in-schema-multiple",
        "unremovable-in-schema-nested",
    ],
)
def test_unrecurse(request, schema, snapshot_json):
    recursive_references = {
        "#/definitions/NestedPerson",
        "#/definitions/A",
        "#/definitions/B",
    }
    moved_schemas = {
        "-definitions-NestedPerson": RECURSIVE_NESTED,
        "-definitions-A": {"anyOf": [{"type": "object"}, {"$ref": "#/definitions/B"}]},
        "-definitions-B": {"anyOf": [{"type": "object"}, {"$ref": "#/definitions/A"}]},
    }
    if "unremovable" in request.node.callspec.id:
        moved_schemas["-definitions-UnremovableNestedPerson"] = RECURSIVE_NESTED_UNREMOVABLE
        moved_schemas["-definitions-UnremovableNestedPerson-Cycle-A"] = RECURSIVE_NESTED_UNREMOVABLE_CYCLE_A
        moved_schemas["-definitions-UnremovableNestedPerson-Cycle-B"] = RECURSIVE_NESTED_UNREMOVABLE_CYCLE_B
        recursive_references.add("#/definitions/UnremovableNestedPerson")
        recursive_references.add("#/definitions/UnremovableNestedPerson-Cycle-A")
        recursive_references.add("#/definitions/UnremovableNestedPerson-Cycle-B")
    cache = TransformCache(recursive_references=recursive_references, moved_schemas=moved_schemas)
    schemas = {"-definitions-Root": schema}
    unrecurse(schemas, cache)
    assert schemas["-definitions-Root"] == snapshot_json
