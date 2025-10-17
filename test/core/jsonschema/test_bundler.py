import pytest

from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, bundle
from schemathesis.core.jsonschema.bundler import BundleError
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.definitions import OPENAPI_30, OPENAPI_31, SWAGGER_20

USER = {"type": "string"}
COMPANY = {"type": "object"}
DEFINITIONS = {
    "definitions": {
        "User": USER,
        "Company": COMPANY,
    }
}


@pytest.mark.parametrize(
    ["schema", "store", "expected"],
    [
        (True, {}, True),
        (False, {}, False),
        ({}, {}, {}),
        (
            {"type": "string", "minLength": 1},
            DEFINITIONS,
            {"type": "string", "minLength": 1},
        ),
        ({"$ref": "#/definitions/User"}, DEFINITIONS, USER),
        (
            {"$ref": "#/definitions/User"},
            {"definitions": {"User": True}},
            # "Truthy" schema is equal to an empty one
            {},
        ),
        (
            {
                "$ref": "#/definitions/User",
                "description": "A user",
                "title": "User Schema",
            },
            DEFINITIONS,
            {"description": "A user", "title": "User Schema", **USER},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "user": {"$ref": "#/definitions/User"},
                    "company": {"$ref": "#/definitions/Company"},
                },
            },
            DEFINITIONS,
            {
                "type": "object",
                "properties": {
                    "user": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},
                    "company": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema2"},
                },
                BUNDLE_STORAGE_KEY: {
                    "schema1": USER,
                    "schema2": COMPANY,
                },
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "user1": {"$ref": "#/definitions/User"},
                    "user2": {"$ref": "#/definitions/User"},
                },
            },
            DEFINITIONS,
            {
                "type": "object",
                "properties": {
                    "user1": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},
                    "user2": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},
                },
                BUNDLE_STORAGE_KEY: {"schema1": USER},
            },
        ),
        (
            {
                "type": "array",
                "items": {"$ref": "#/definitions/User"},
            },
            DEFINITIONS,
            {
                "type": "array",
                "items": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},
                BUNDLE_STORAGE_KEY: {"schema1": USER},
            },
        ),
        (
            {
                "anyOf": [
                    {"$ref": "#/definitions/User"},
                    {"$ref": "#/definitions/Company"},
                ]
            },
            DEFINITIONS,
            {
                "anyOf": [
                    {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},
                    {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema2"},
                ],
                BUNDLE_STORAGE_KEY: {
                    "schema1": USER,
                    "schema2": COMPANY,
                },
            },
        ),
        (
            {"$ref": "#/definitions/User"},
            {
                "definitions": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "company": {"$ref": "#/definitions/Company"},
                        },
                    },
                    "Company": {"type": "string"},
                }
            },
            {
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
                BUNDLE_STORAGE_KEY: {
                    "schema1": {
                        "type": "object",
                        "properties": {
                            "company": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema2"},
                        },
                    },
                    "schema2": {"type": "string"},
                },
            },
        ),
        (
            {"$ref": "#/definitions/Node"},
            {
                "definitions": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "child": {"$ref": "#/definitions/Node"},
                        },
                    }
                },
            },
            {
                "type": "object",
                "properties": {
                    "child": {
                        # Inlined 1 level
                        "properties": {},
                        "type": "object",
                    },
                },
            },
        ),
        (
            {"$ref": "#/definitions/A"},
            {
                "definitions": {
                    "A": {
                        "type": "object",
                        "properties": {
                            "b": {"$ref": "#/definitions/B"},
                        },
                    },
                    "B": {
                        "type": "object",
                        "properties": {
                            "a": {
                                # Inlined 1 level
                                "properties": {},
                                "type": "object",
                            },
                        },
                    },
                }
            },
            {
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
                BUNDLE_STORAGE_KEY: {
                    "schema1": {
                        "type": "object",
                        "properties": {
                            "b": {
                                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema2",
                            }
                        },
                    },
                    "schema2": {
                        "type": "object",
                        "properties": {
                            "a": {
                                # Inlined 1 level
                                "properties": {},
                                "type": "object",
                            }
                        },
                    },
                },
            },
        ),
        (
            {
                "definitions": {
                    "schema": {
                        "properties": {
                            "key": {
                                "anyOf": [
                                    {"$ref": "#/definitions/schema"},
                                    {
                                        "items": {},
                                    },
                                ]
                            }
                        }
                    }
                }
            },
            {
                "definitions": {
                    "schema": {
                        "properties": {
                            "key": {
                                "anyOf": [
                                    {"$ref": "#/definitions/schema"},
                                    {
                                        "items": {},
                                    },
                                ]
                            }
                        }
                    }
                }
            },
            {
                "definitions": {
                    "schema": {
                        "properties": {
                            "key": {
                                "anyOf": [
                                    {
                                        "$ref": "#/x-bundled/schema1",
                                    },
                                    {
                                        "items": {},
                                    },
                                ],
                            },
                        },
                    },
                },
                "x-bundled": {
                    "schema1": {
                        "properties": {
                            "key": {
                                "anyOf": [
                                    {
                                        "properties": {},
                                    },
                                    {
                                        "items": {},
                                    },
                                ],
                            },
                        },
                    },
                },
            },
        ),
        (
            {
                "definitions": {
                    "vendorExtension": {},
                    "schema": {
                        "patternProperties": {"$ref": "#/definitions/vendorExtension"},
                        "properties": {"schema": {"$ref": "#/definitions/schema"}},
                    },
                }
            },
            {
                "definitions": {
                    "vendorExtension": {},
                    "schema": {
                        "patternProperties": {"$ref": "#/definitions/vendorExtension"},
                        "properties": {"schema": {"$ref": "#/definitions/schema"}},
                    },
                }
            },
            {
                "definitions": {
                    "schema": {
                        "patternProperties": {
                            "$ref": "#/x-bundled/schema1",
                        },
                        "properties": {
                            "schema": {
                                "$ref": "#/x-bundled/schema2",
                            },
                        },
                    },
                    "vendorExtension": {},
                },
                "x-bundled": {
                    "schema1": {},
                    "schema2": {
                        "patternProperties": {
                            "$ref": "#/x-bundled/schema1",
                        },
                        "properties": {
                            "schema": {
                                "patternProperties": {
                                    "$ref": "#/x-bundled/schema1",
                                },
                                "properties": {},
                            },
                        },
                    },
                },
            },
        ),
        (
            {"$ref": "#/components/schemas/Query"},
            {
                "components": {
                    "schemas": {
                        "ArrayExpression": {},
                        "Expression": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/ArrayExpression"},
                                {"$ref": "#/components/schemas/MemberExpression"},
                            ]
                        },
                        "MemberExpression": {
                            "properties": {
                                "key": {"$ref": "#/components/schemas/Expression"},
                            }
                        },
                        "Query": {"$ref": "#/components/schemas/Expression"},
                    }
                }
            },
            {
                "$ref": "#/x-bundled/schema1",
                "x-bundled": {
                    "schema1": {
                        "$ref": "#/x-bundled/schema2",
                    },
                    "schema2": {
                        "oneOf": [
                            {
                                "$ref": "#/x-bundled/schema3",
                            },
                            {
                                "$ref": "#/x-bundled/schema4",
                            },
                        ],
                    },
                    "schema3": {},
                    "schema4": {
                        "properties": {
                            "key": {
                                # Inlined recursive reference
                                "oneOf": [
                                    {
                                        "$ref": "#/x-bundled/schema3",
                                    },
                                    {
                                        "properties": {},
                                    },
                                ],
                            },
                        },
                    },
                },
            },
        ),
    ],
)
def test_bundle(schema, store, expected):
    resolver = RefResolver.from_schema(store)
    assert bundle(schema, resolver, inline_recursive=True).schema == expected


def test_unresolvable_pointer():
    resolver = RefResolver.from_schema({})
    with pytest.raises(RefResolutionError):
        bundle({"$ref": "#/definitions/NonExistent"}, resolver, inline_recursive=True)


def test_bundle_ref_resolves_to_none_error_message():
    resolver = RefResolver.from_schema({"definitions": {"User": None}})
    with pytest.raises(BundleError) as exc:
        bundle({"$ref": "#/definitions/User"}, resolver, inline_recursive=True)
    assert str(exc.value) == "Cannot bundle `#/definitions/User`: expected JSON Schema (object or boolean), got null"


def test_bundle_recursive_not_inlined():
    # When recursive references are not inlined via inline_recursive=False
    schema = {"$ref": "#/definitions/Node"}
    store = {
        "definitions": {
            "Node": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/definitions/Node"},
                },
            }
        },
    }

    resolver = RefResolver.from_schema(store)

    assert bundle(schema, resolver, inline_recursive=False).schema == {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
        BUNDLE_STORAGE_KEY: {
            "schema1": {
                "type": "object",
                "properties": {
                    "child": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},  # Self-reference preserved
                },
            }
        },
    }


def test_bundle_non_recursive_inlined():
    # When non-recursive references are not inlined via inline_recursive=False
    schema = {"$ref": "#/definitions/User"}
    store = {
        "definitions": {
            "User": {"type": "object"},
        },
    }

    resolver = RefResolver.from_schema(store)

    assert bundle(schema, resolver, inline_recursive=False).schema == {"type": "object"}


@pytest.mark.parametrize("schema", [SWAGGER_20, OPENAPI_30, OPENAPI_31])
def test_bundles_open_api_schemas(schema):
    # This is a smoke test, they should be bundled without errors
    resolver = RefResolver.from_schema(deepclone(schema))
    bundle(deepclone(schema), resolver, inline_recursive=True)


def test_bundle_infinite_recursive_required_cycle_message():
    schema = {"$ref": "#/definitions/A"}
    store = {
        "definitions": {
            "A": {
                "type": "object",
                "properties": {"b": {"$ref": "#/definitions/B"}},
                "required": ["b"],  # cannot remove `b` without breaking A
            },
            "B": {
                "type": "object",
                "properties": {"c": {"$ref": "#/definitions/C"}},
                "required": ["c"],
            },
            "C": {
                "type": "object",
                "properties": {"a": {"$ref": "#/definitions/A"}},
                "required": ["a"],
            },
        }
    }

    resolver = RefResolver.from_schema(store)

    with pytest.raises(InfiniteRecursiveReference) as exc:
        bundle(schema, resolver, inline_recursive=True)

    assert (
        str(exc.value)
        == """Schema `#/definitions/A` has required references forming a cycle:

  #/definitions/A ->
  #/definitions/B ->
  #/definitions/C ->
  #/definitions/A"""
    )
