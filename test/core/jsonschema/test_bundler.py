from typing import Any

import pytest

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, bundle, bundle_for_generation, bundle_for_validation
from schemathesis.core.jsonschema.bundler import BundleError, unbundle, unbundle_path
from schemathesis.core.jsonschema.resolver import make_root_resolver
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
    ids=[
        "true-schema",
        "false-schema",
        "empty-schema",
        "scalar-no-ref",
        "single-ref",
        "ref-to-truthy",
        "ref-with-siblings",
        "multiple-distinct-refs",
        "deduplicated-refs",
        "array-items-ref",
        "anyof-refs",
        "nested-ref-chain",
        "self-recursive",
        "mutual-recursion",
        "preserves-existing-definitions",
        "patternproperties-with-recursive-ref",
        "query-expression-deep-recursion",
    ],
)
def test_bundle(schema, store, expected):
    resolver = make_root_resolver(store)
    assert bundle(schema, resolver, inline_recursive=True).schema == expected


def test_unresolvable_pointer():
    resolver = make_root_resolver({})
    with pytest.raises(RefResolutionError):
        bundle({"$ref": "#/definitions/NonExistent"}, resolver, inline_recursive=True)


def test_bundle_ref_resolves_to_none_error_message():
    resolver = make_root_resolver({"definitions": {"User": None}})
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

    resolver = make_root_resolver(store)

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


def test_bundle_for_generation_inlines_recursive_references():
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

    resolver = make_root_resolver(store)

    assert bundle_for_generation(schema, resolver).schema == {
        "type": "object",
        "properties": {
            "child": {
                "properties": {},
                "type": "object",
            },
        },
    }


def test_bundle_for_validation_preserves_recursive_references():
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

    resolver = make_root_resolver(store)

    assert bundle_for_validation(schema, resolver).schema == {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
        BUNDLE_STORAGE_KEY: {
            "schema1": {
                "type": "object",
                "properties": {
                    "child": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"},
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

    resolver = make_root_resolver(store)

    assert bundle(schema, resolver, inline_recursive=False).schema == {"type": "object"}


def _strip_remote_refs(value: Any) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith(("http://", "https://")):
            return {}
        return {key: _strip_remote_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_remote_refs(item) for item in value]
    return value


@pytest.mark.parametrize("schema", [SWAGGER_20, OPENAPI_30, OPENAPI_31])
def test_bundles_open_api_schemas(schema):
    # Smoke test: official meta-schemas bundle without errors. Remote refs are stripped
    # so the test stays offline.
    schema = _strip_remote_refs(schema)
    resolver = make_root_resolver(schema)
    bundle(schema, resolver, inline_recursive=True)


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

    resolver = make_root_resolver(store)

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


def test_bundle_self_recursion_through_pattern_properties_is_breakable():
    # An object `{}` validates against `A`, so the cycle through `patternProperties`
    # is structurally optional and should not raise.
    schema = {"$ref": "#/definitions/A"}
    store = {
        "definitions": {
            "A": {
                "type": "object",
                "patternProperties": {".*": {"$ref": "#/definitions/A"}},
                "additionalProperties": False,
            },
        }
    }

    resolver = make_root_resolver(store)

    bundle(schema, resolver, inline_recursive=True)


def test_bundle_self_cycle_through_dead_definitions_block():
    # Self-referential `definitions` entries are unreachable once optional properties
    # are pruned, so they should not surface as `InfiniteRecursiveReference`.
    schema = {"$ref": "#/definitions/Meta"}
    store = {
        "definitions": {
            "Meta": {
                "type": "object",
                "definitions": {
                    "schemaArray": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"$ref": "#/definitions/Meta"},
                    },
                },
                "properties": {
                    "allOf": {"$ref": "#/definitions/Meta/definitions/schemaArray"},
                    "items": {"$ref": "#/definitions/Meta"},
                },
            },
        }
    }

    resolver = make_root_resolver(store)

    bundle(schema, resolver, inline_recursive=True)


def test_bundle_oneof_with_indirectly_recursive_branch_skips_it():
    # An `oneOf` variant whose body cycles back through a deeper required path is
    # breakable when at least one terminating variant remains.
    schema = {"$ref": "#/definitions/Types"}
    store = {
        "definitions": {
            "Types": {
                "oneOf": [
                    {"$ref": "#/definitions/PrimitiveType"},
                    {"$ref": "#/definitions/Record"},
                ]
            },
            "PrimitiveType": {"type": "string", "enum": ["int", "string"]},
            "Record": {
                "type": "object",
                "required": ["fields"],
                "properties": {
                    "fields": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"$ref": "#/definitions/Types"},
                    }
                },
            },
        }
    }

    resolver = make_root_resolver(store)

    bundle(schema, resolver, inline_recursive=True)


def test_bundle_oneof_with_self_ref_picks_non_recursive_branch():
    # `oneOf` with a non-recursive variant alongside a self-`$ref` is breakable.
    schema = {"$ref": "#/definitions/configItemsType"}
    store = {
        "definitions": {
            "simpleConfigType": {"type": "string"},
            "configItemsType": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {
                        "oneOf": [
                            {"$ref": "#/definitions/simpleConfigType"},
                            {"$ref": "#/definitions/configItemsType"},
                        ]
                    }
                },
            },
        }
    }

    resolver = make_root_resolver(store)

    bundle(schema, resolver, inline_recursive=True)


def test_bundle_allof_with_self_ref_drops_trivial_self_constraint():
    # A self-`$ref` in the schema's own top-level `allOf` is trivially satisfied,
    # so it should not turn the schema into an unbreakable cycle.
    schema = {"$ref": "#/definitions/Node"}
    store = {
        "definitions": {
            "Node": {
                "type": "object",
                "allOf": [
                    {"$ref": "#/definitions/Node"},
                    {"properties": {"name": {"type": "string"}}},
                ],
            },
        }
    }

    resolver = make_root_resolver(store)

    bundle(schema, resolver, inline_recursive=True)


def test_bundle_mutual_cycle_through_pattern_properties_is_breakable():
    # Mutual cycle terminated by an empty object that satisfies `patternProperties`
    # (no `minProperties`) and an `oneOf` branch that doesn't recurse.
    schema = {"$ref": "#/definitions/KitNode"}
    store = {
        "definitions": {
            "KitNode": {
                "oneOf": [
                    {"$ref": "#/definitions/KitContainer"},
                    {"$ref": "#/definitions/KitItem"},
                ]
            },
            "KitContainer": {
                "type": "object",
                "required": ["children"],
                "properties": {
                    "children": {
                        "type": "object",
                        "patternProperties": {".*": {"$ref": "#/definitions/KitNode"}},
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "KitItem": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            },
        }
    }

    resolver = make_root_resolver(store)

    bundle(schema, resolver, inline_recursive=True)


def test_unbundle_decodes_pointer_escaping_in_definition_names():
    # Definition name with a literal `/` is encoded as `~1` in the URI fragment.
    # Unbundling should recover the original key, not the encoded form.
    name_to_uri = {"schema1": "#/definitions/User~1Profile"}
    bundled = {
        "$ref": "#/x-bundled/schema1",
        BUNDLE_STORAGE_KEY: {"schema1": {"type": "object"}},
    }
    result = unbundle(bundled, name_to_uri)
    assert result["components"]["schemas"] == {"User/Profile": {"type": "object"}}


def test_unbundle_path_decodes_pointer_escaping():
    # Path segments reconstructed from a URI fragment must be JSON-Pointer-decoded.
    name_to_uri = {"schema1": "#/definitions/User~1Profile"}
    assert unbundle_path([BUNDLE_STORAGE_KEY, "schema1", "properties", "id"], name_to_uri) == [
        "definitions",
        "User/Profile",
        "properties",
        "id",
    ]
