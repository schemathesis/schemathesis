import pytest

from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, bundle
from schemathesis.core.jsonschema.bundler import BundleError

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
        (
            {"$ref": "#/definitions/User"},
            DEFINITIONS,
            {
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
                BUNDLE_STORAGE_KEY: {"schema1": USER},
            },
        ),
        (
            {
                "$ref": "#/definitions/User",
                "description": "A user",
                "title": "User Schema",
            },
            DEFINITIONS,
            {
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
                "description": "A user",
                "title": "User Schema",
                BUNDLE_STORAGE_KEY: {"schema1": USER},
            },
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
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
                BUNDLE_STORAGE_KEY: {
                    "schema1": {
                        "type": "object",
                        "properties": {
                            "child": {
                                # Inlined 1 level
                                "properties": {},
                                "type": "object",
                            },
                        },
                    }
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
    ],
)
def test_bundle(schema, store, expected):
    resolver = RefResolver.from_schema(store)
    assert bundle(schema, resolver) == expected


def test_unresolvable_pointer():
    resolver = RefResolver.from_schema({})
    with pytest.raises(RefResolutionError):
        bundle({"$ref": "#/definitions/NonExistent"}, resolver)


def test_bundle_ref_resolves_to_none_error_message():
    resolver = RefResolver.from_schema({"definitions": {"User": None}})
    with pytest.raises(BundleError) as exc:
        bundle({"$ref": "#/definitions/User"}, resolver)
    assert str(exc.value) == "Cannot bundle `#/definitions/User`: expected JSON Schema (object or boolean), got null"
