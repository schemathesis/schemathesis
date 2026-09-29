from typing import Any

import pytest

from schemathesis.core.errors import RefResolutionError
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, Bundler, bundle
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
                    "user": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
                    "company": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000002"},
                },
                BUNDLE_STORAGE_KEY: {
                    "schema000001": USER,
                    "schema000002": COMPANY,
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
                    "user1": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
                    "user2": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
                },
                BUNDLE_STORAGE_KEY: {"schema000001": USER},
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
                "items": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
                BUNDLE_STORAGE_KEY: {"schema000001": USER},
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
                    {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
                    {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000002"},
                ],
                BUNDLE_STORAGE_KEY: {
                    "schema000001": USER,
                    "schema000002": COMPANY,
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
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001",
                BUNDLE_STORAGE_KEY: {
                    "schema000001": {
                        "type": "object",
                        "properties": {
                            "company": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000002"},
                        },
                    },
                    "schema000002": {"type": "string"},
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
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001",
                BUNDLE_STORAGE_KEY: {
                    "schema000001": {
                        "type": "object",
                        "properties": {"child": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"}},
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
                        "properties": {"a": {"$ref": "#/definitions/A"}},
                    },
                }
            },
            {
                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001",
                BUNDLE_STORAGE_KEY: {
                    "schema000001": {
                        "type": "object",
                        "properties": {
                            "b": {
                                "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000002",
                            }
                        },
                    },
                    "schema000002": {
                        "type": "object",
                        "properties": {"a": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"}},
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
                                        "$ref": "#/x-bundled/schema000001",
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
                    "schema000001": {
                        "properties": {
                            "key": {
                                "anyOf": [
                                    {"$ref": "#/x-bundled/schema000001"},
                                    {"items": {}},
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
                            "$ref": "#/x-bundled/schema000001",
                        },
                        "properties": {
                            "schema": {
                                "$ref": "#/x-bundled/schema000002",
                            },
                        },
                    },
                    "vendorExtension": {},
                },
                "x-bundled": {
                    "schema000001": {},
                    "schema000002": {
                        "patternProperties": {
                            "$ref": "#/x-bundled/schema000001",
                        },
                        "properties": {
                            "schema": {"$ref": "#/x-bundled/schema000002"},
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
                "$ref": "#/x-bundled/schema000001",
                "x-bundled": {
                    "schema000001": {
                        "$ref": "#/x-bundled/schema000002",
                    },
                    "schema000002": {
                        "oneOf": [
                            {
                                "$ref": "#/x-bundled/schema000003",
                            },
                            {
                                "$ref": "#/x-bundled/schema000004",
                            },
                        ],
                    },
                    "schema000003": {},
                    "schema000004": {
                        "properties": {
                            "key": {"$ref": "#/x-bundled/schema000002"},
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
    assert Bundler().bundle(schema, resolver).schema == expected


def test_unresolvable_pointer():
    resolver = make_root_resolver({})
    with pytest.raises(RefResolutionError):
        Bundler().bundle({"$ref": "#/definitions/NonExistent"}, resolver)


def test_bundle_ref_resolves_to_none_error_message():
    resolver = make_root_resolver({"definitions": {"User": None}})
    with pytest.raises(BundleError) as exc:
        Bundler().bundle({"$ref": "#/definitions/User"}, resolver)
    assert str(exc.value) == "Cannot bundle `#/definitions/User`: expected JSON Schema (object or boolean), got null"


def test_bundle_recursive_not_inlined():
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

    assert Bundler().bundle(schema, resolver).schema == {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001",
        BUNDLE_STORAGE_KEY: {
            "schema000001": {
                "type": "object",
                "properties": {
                    "child": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},  # Self-reference preserved
                },
            }
        },
    }


def test_bundle_preserves_recursive_references():
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

    assert bundle(schema, resolver).schema == {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001",
        BUNDLE_STORAGE_KEY: {
            "schema000001": {
                "type": "object",
                "properties": {
                    "child": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
                },
            }
        },
    }


def test_bundle_non_recursive_inlined():
    schema = {"$ref": "#/definitions/User"}
    store = {
        "definitions": {
            "User": {"type": "object"},
        },
    }

    resolver = make_root_resolver(store)

    assert Bundler().bundle(schema, resolver).schema == {"type": "object"}


def test_bundle_not_inlined_when_a_sibling_also_references_the_target():
    # Inlining the root `$ref` must not strip storage a sibling reference still points at.
    schema = {"$ref": "#/definitions/User", "allOf": [{"$ref": "#/definitions/User"}]}
    store = {"definitions": {"User": {"type": "object"}}}

    resolver = make_root_resolver(store)

    assert Bundler().bundle(schema, resolver).schema == {
        "$ref": "#/x-bundled/schema000001",
        "allOf": [{"$ref": "#/x-bundled/schema000001"}],
        BUNDLE_STORAGE_KEY: {"schema000001": {"type": "object"}},
    }


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
    Bundler().bundle(schema, resolver)


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

    bundled = Bundler().bundle(schema, resolver).schema

    assert bundled["$ref"] == f"#/{BUNDLE_STORAGE_KEY}/schema000001"
    assert bundled[BUNDLE_STORAGE_KEY]["schema000001"]["properties"]["b"] == {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000002"
    }


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

    Bundler().bundle(schema, resolver)


def test_bundle_self_cycle_through_dead_definitions_block():
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

    Bundler().bundle(schema, resolver)


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

    Bundler().bundle(schema, resolver)


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

    Bundler().bundle(schema, resolver)


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

    Bundler().bundle(schema, resolver)


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

    Bundler().bundle(schema, resolver)


def test_unbundle_decodes_pointer_escaping_in_definition_names():
    # Definition name with a literal `/` is encoded as `~1` in the URI fragment.
    # Unbundling should recover the original key, not the encoded form.
    name_to_uri = {"schema000001": "#/definitions/User~1Profile"}
    bundled = {
        "$ref": "#/x-bundled/schema000001",
        BUNDLE_STORAGE_KEY: {"schema000001": {"type": "object"}},
    }
    result = unbundle(bundled, name_to_uri)
    assert result["components"]["schemas"] == {"User/Profile": {"type": "object"}}


def test_unbundle_path_decodes_pointer_escaping():
    # Path segments reconstructed from a URI fragment must be JSON-Pointer-decoded.
    name_to_uri = {"schema000001": "#/definitions/User~1Profile"}
    assert unbundle_path([BUNDLE_STORAGE_KEY, "schema000001", "properties", "id"], name_to_uri) == [
        "definitions",
        "User/Profile",
        "properties",
        "id",
    ]


def test_bundle_resolves_reference_by_declared_id():
    # A subschema that names itself is reachable by that name in-document, with nothing fetched.
    resolver = make_root_resolver(
        {"definitions": {"Text": {"$id": "https://example.invalid/text.json", "type": "string"}}}
    )
    assert Bundler().bundle({"$ref": "https://example.invalid/text.json"}, resolver).schema == {"type": "string"}


def test_bundle_drops_id_from_bundled_definitions():
    # A legacy fragment-only `$id` sets a base URI that later `#/x-bundled/...` lookups cannot resolve against.
    resolver = make_root_resolver({"definitions": {"Text": {"$id": "#/definitions/text", "type": "string"}}})
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "#/definitions/Text"}, "b": {"$ref": "#/definitions/Text"}},
    }
    assert Bundler().bundle(schema, resolver).schema == {
        "type": "object",
        "properties": {
            "a": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
            "b": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema000001"},
        },
        BUNDLE_STORAGE_KEY: {"schema000001": {"type": "string"}},
    }


@pytest.mark.parametrize("already_bundled", [0, 5, 95])
def test_bundle_names_sort_in_creation_order(already_bundled):
    # Canonicalization orders definitions by name, so names that sort differently depending on how
    # much was bundled earlier give the same schema a different shape per load order.
    definitions = {
        f"D{idx}": {"type": "object", "properties": {"next": {"$ref": f"#/definitions/D{idx + 1}"}}}
        for idx in range(12)
    }
    definitions["D12"] = {"type": "string"}
    resolver = make_root_resolver({"definitions": definitions})
    bundler = Bundler()
    bundler.counter = already_bundled

    names = list(bundler.bundle({"$ref": "#/definitions/D0"}, resolver).schema[BUNDLE_STORAGE_KEY])

    assert sorted(names) == sorted(names, key=lambda name: int(name.removeprefix("schema")))
