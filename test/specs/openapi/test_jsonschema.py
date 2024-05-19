import json
from unittest.mock import ANY

import pytest
from hypothesis import HealthCheck, Phase, Verbosity, given, settings
from hypothesis_jsonschema import from_schema
from pytest_httpserver.pytest_plugin import PluginHTTPServer
from referencing.jsonschema import DRAFT4
from referencing import Registry, Resource

from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._jsonschema import (
    get_remote_schema_retriever,
    to_jsonschema,
    TransformConfig,
)

REMOTE_PLACEHOLDER = "http://example.com"
DEFAULT_URI = ""
FILE_URI = object()
REMOTE_URI = object()
TARGET = {"type": "integer"}
TARGET_LOCAL_REF = {"$ref": "#/components/schemas/Example"}
TARGET_LOCAL_NESTED_LOCAL_REF = {"$ref": "#/components/schemas/Nested"}
TARGET_LOCAL_NESTED_FILE_REF = {"$ref": "#/components/schemas/Nested-File"}
TARGET_LOCAL_NESTED_REMOTE_REF = {"$ref": "#/components/schemas/Nested-Remote"}
TARGET_FILE_REF = {"$ref": "root-components.json#/RootItem"}
TARGET_RELATIVE_FILE_REF = {"$ref": "../relative-components.json#/RelativeItem"}
TARGET_FILE_WITH_SCHEME_REF = {"$ref": "file://root-components.json#/RootItem"}
# Directory structure for scoped references looks like this:
# /
# ├── root.json
# ├── nested-2
# │   └── components.json
# └── nested-1
#     └── components.json     # The test example starts here
#
# The schema in nested-1/components.json references the schema in nested-2/components.json
# via a relative reference. It requires a proper scope because the root schema is in the parent directory,
# and without the notion of scope it would be pointing to the parent of the root directory of the test.
NESTED_SCOPE = object()
TARGET_FILE_WITH_SCOPED_FILE_REF = {"$ref": "../nested-2/components.json#/Nested-2"}
TARGET_FILE_ROOT_SCHEMA = {"RootItem": TARGET}
TARGET_FILE_RELATIVE_SCHEMA = {"RelativeItem": TARGET}
TARGET_FILE_NESTED_2_SCHEMA = {"Nested-2": TARGET}
TARGET_REMOTE_REF = {"$ref": f"{REMOTE_PLACEHOLDER}/schema.json#/RootItem"}
TARGET_REMOTE_ROOT_SCHEMA = {"RootItem": TARGET}
# Local references
LOCAL_REF_NO_NESTING = TARGET_LOCAL_REF
LOCAL_REF_NESTED_IN_OBJECT = {"properties": {"example": TARGET_LOCAL_REF}}
LOCAL_NESTED_REF_NESTED_IN_OBJECT = {"properties": {"example": TARGET_LOCAL_NESTED_LOCAL_REF}}
LOCAL_REF_NESTED_IN_OBJECT_MULTIPLE = {
    "properties": {"example-1": TARGET_LOCAL_REF, "example-2": TARGET_LOCAL_REF},
}
LOCAL_NESTED_REF_NESTED_IN_OBJECT_MULTIPLE = {
    "properties": {"example-1": TARGET_LOCAL_NESTED_LOCAL_REF, "example-2": TARGET_LOCAL_NESTED_LOCAL_REF},
}
LOCAL_REF_NESTED_IN_ARRAY = {"allOf": [TARGET_LOCAL_REF]}
LOCAL_REF_NESTED_IN_ARRAY_MULTIPLE = {"allOf": [TARGET_LOCAL_REF, TARGET_LOCAL_REF]}
# File references
FILE_REF_NO_NESTING = TARGET_FILE_REF
FILE_REF_WITH_SCHEME_NO_NESTING = TARGET_FILE_WITH_SCHEME_REF
FILE_REF_NESTED_IN_OBJECT = {"properties": {"example": TARGET_FILE_REF}}
FILE_NESTED_FILE_REF_NESTED_IN_OBJECT = {"properties": {"example": TARGET_LOCAL_NESTED_FILE_REF}}
FILE_NESTED_FILE_REF_IN_OBJECT_MULTIPLE = {
    "properties": {"example-1": TARGET_LOCAL_NESTED_FILE_REF, "example-2": TARGET_LOCAL_NESTED_FILE_REF},
}
FILE_RELATIVE_REF = TARGET_RELATIVE_FILE_REF
FILE_SCOPED_REF = {"properties": {"nested-2": TARGET_FILE_WITH_SCOPED_FILE_REF}}
# Remote references
REMOTE_REF_NO_NESTING = TARGET_REMOTE_REF
REMOTE_REF_NESTED_IN_OBJECT = {"properties": {"example": TARGET_REMOTE_REF}}
REMOTE_REF_NESTED_IN_OBJECT_MULTIPLE = {
    "properties": {"example-1": TARGET_REMOTE_REF, "example-2": TARGET_REMOTE_REF},
}
# Inner references
INNER_REF = {
    "properties": {
        "example": {
            "$ref": "#/definitions/Example",
        },
    },
    "definitions": {
        "Example": TARGET,
    },
}
INNER_REF_WITH_NESTED_FILE_REF = {
    "properties": {
        "example": {
            "$ref": "#/definitions/Example",
        }
    },
    "definitions": {
        "Example": TARGET_FILE_REF,
    },
}
# Recursive references
RECURSION_SCHEMA_ONE_HOP = {"$ref": "#/definitions/SchemaA"}


@pytest.fixture(scope="module")
def httpserver():
    server = PluginHTTPServer(host="127.0.0.1", port=0)
    server.start()
    yield server
    if server.is_running():
        server.stop()


def makefile(directory, name, schema):
    target = directory
    for entry in name.split("/")[:-1]:
        target = target / entry
        target.ensure_dir()
    filename = name.split("/")[-1]
    (target / filename).write_text(json.dumps(schema), "utf8")


def setup_schema(request, uri, scope, schema):
    schema = fast_deepcopy(schema)
    if uri is FILE_URI:
        testdir = request.getfixturevalue("testdir")
        root = testdir.mkdir("root")
        makefile(root, "root-components.json", TARGET_FILE_ROOT_SCHEMA)
        makefile(root, "/nested-2/components.json", TARGET_FILE_NESTED_2_SCHEMA)
        testdir.makefile(".json", **{"relative-components": json.dumps(TARGET_FILE_RELATIVE_SCHEMA)})
        uri = str(root / "schema.json")
        if scope is NESTED_SCOPE:
            # It is not necessary for this file to exist, we assume that the schema is already loaded from there
            scope = str(root / "nested-1/components.json")
    elif uri is REMOTE_URI:
        server = request.getfixturevalue("httpserver")
        server.expect_request("/schema.json").respond_with_json(TARGET_REMOTE_ROOT_SCHEMA)
        uri = f"http://{server.host}:{server.port}"
        prepared = json.dumps(schema).replace(REMOTE_PLACEHOLDER, uri)
        schema = json.loads(prepared)
    return uri, scope, schema


@pytest.mark.parametrize(
    (
        # Schema URI
        "uri",
        # Scope in which the schema was resolved
        # This is needed to properly handle relative references
        "scope",
        # Parameter schema intended for data generation
        "schema",
        # Components shared between different operations
        "components",
        "expected",
    ),
    (
        (DEFAULT_URI, "", True, {}, True),
        (
            DEFAULT_URI,
            "",
            {},
            {},
            {},
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_REF_NO_NESTING,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                    },
                }
            },
            {
                "$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795",
                "x-moved-references": {"aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"}},
            },
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_REF_NESTED_IN_OBJECT,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                    },
                },
            },
            {
                "properties": {"example": {"$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"}},
                "x-moved-references": {"aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"}},
            },
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_NESTED_REF_NESTED_IN_OBJECT,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                        "Nested": TARGET_LOCAL_REF,
                    },
                },
            },
            {
                "properties": {"example": {"$ref": "#/x-moved-references/58d4bb06ad165cda74c28d601b154ace1019890c"}},
                "x-moved-references": {
                    "58d4bb06ad165cda74c28d601b154ace1019890c": {
                        "$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"
                    },
                    "aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"},
                },
            },
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_REF_NESTED_IN_OBJECT_MULTIPLE,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                        "Nested": TARGET_LOCAL_REF,
                    },
                },
            },
            {
                "properties": {
                    "example-1": {"$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"},
                    "example-2": {"$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"},
                },
                "x-moved-references": {"aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"}},
            },
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_NESTED_REF_NESTED_IN_OBJECT_MULTIPLE,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                        "Nested": TARGET_LOCAL_REF,
                    },
                },
            },
            {
                "properties": {
                    "example-1": {"$ref": "#/x-moved-references/58d4bb06ad165cda74c28d601b154ace1019890c"},
                    "example-2": {"$ref": "#/x-moved-references/58d4bb06ad165cda74c28d601b154ace1019890c"},
                },
                "x-moved-references": {
                    "58d4bb06ad165cda74c28d601b154ace1019890c": {
                        "$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"
                    },
                    "aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"},
                },
            },
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_REF_NESTED_IN_ARRAY,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                    },
                },
            },
            {
                "allOf": [{"$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"}],
                "x-moved-references": {"aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"}},
            },
        ),
        (
            DEFAULT_URI,
            "",
            LOCAL_REF_NESTED_IN_ARRAY_MULTIPLE,
            {
                "components": {
                    "schemas": {
                        "Example": TARGET,
                    },
                },
            },
            {
                "allOf": [
                    {"$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"},
                    {"$ref": "#/x-moved-references/aa54005f4a84cceab1fb666434aba9aa1a1bc795"},
                ],
                "x-moved-references": {"aa54005f4a84cceab1fb666434aba9aa1a1bc795": {"type": "integer"}},
            },
        ),
        (
            FILE_URI,
            "",
            FILE_REF_NO_NESTING,
            {},
            {
                "$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719",
                "x-moved-references": {"77c17a5efa18bdd0d75b1b8686d8daf4f881c719": {"type": "integer"}},
            },
        ),
        (
            FILE_URI,
            "",
            FILE_REF_WITH_SCHEME_NO_NESTING,
            {},
            {
                "$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719",
                "x-moved-references": {"77c17a5efa18bdd0d75b1b8686d8daf4f881c719": {"type": "integer"}},
            },
        ),
        (
            FILE_URI,
            "",
            FILE_REF_NESTED_IN_OBJECT,
            {},
            {
                "properties": {"example": {"$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719"}},
                "x-moved-references": {"77c17a5efa18bdd0d75b1b8686d8daf4f881c719": {"type": "integer"}},
            },
        ),
        (
            FILE_URI,
            "",
            FILE_NESTED_FILE_REF_NESTED_IN_OBJECT,
            {
                "components": {
                    "schemas": {
                        "Nested-File": TARGET_FILE_REF,
                    },
                },
            },
            {
                "properties": {"example": {"$ref": "#/x-moved-references/685e4330057cf6ab44313ea387bdf57a2416782a"}},
                "x-moved-references": {
                    "685e4330057cf6ab44313ea387bdf57a2416782a": {
                        "$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719"
                    },
                    "77c17a5efa18bdd0d75b1b8686d8daf4f881c719": {"type": "integer"},
                },
            },
        ),
        (
            FILE_URI,
            "",
            FILE_NESTED_FILE_REF_IN_OBJECT_MULTIPLE,
            {
                "components": {
                    "schemas": {
                        "Nested-File": TARGET_FILE_REF,
                    },
                },
            },
            {
                "properties": {
                    "example-1": {"$ref": "#/x-moved-references/685e4330057cf6ab44313ea387bdf57a2416782a"},
                    "example-2": {"$ref": "#/x-moved-references/685e4330057cf6ab44313ea387bdf57a2416782a"},
                },
                "x-moved-references": {
                    "685e4330057cf6ab44313ea387bdf57a2416782a": {
                        "$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719"
                    },
                    "77c17a5efa18bdd0d75b1b8686d8daf4f881c719": {"type": "integer"},
                },
            },
        ),
        (
            FILE_URI,
            "",
            FILE_RELATIVE_REF,
            {},
            {
                "$ref": "#/x-moved-references/4f2e7403753928e6b218cb8e72afb242f55ca267",
                "x-moved-references": {"4f2e7403753928e6b218cb8e72afb242f55ca267": {"type": "integer"}},
            },
        ),
        (
            FILE_URI,
            NESTED_SCOPE,
            FILE_SCOPED_REF,
            {},
            {
                "properties": {"nested-2": {"$ref": "#/x-moved-references/6c00c9b97a929ead696fd076eb0f208b33ee9583"}},
                "x-moved-references": {"6c00c9b97a929ead696fd076eb0f208b33ee9583": {"type": "integer"}},
            },
        ),
        (
            REMOTE_URI,
            "",
            REMOTE_REF_NO_NESTING,
            {},
            ANY,
        ),
        (
            REMOTE_URI,
            "",
            REMOTE_REF_NESTED_IN_OBJECT,
            {},
            ANY,
        ),
        (
            REMOTE_URI,
            "",
            REMOTE_REF_NESTED_IN_OBJECT_MULTIPLE,
            {},
            ANY,
        ),
        (
            DEFAULT_URI,
            "",
            INNER_REF,
            {},
            {
                "properties": {"example": {"$ref": "#/x-moved-references/8c3ff8eb23370337fe1f4d50625776ca412cf3ce"}},
                "definitions": {"Example": {"type": "integer"}},
                "x-moved-references": {"8c3ff8eb23370337fe1f4d50625776ca412cf3ce": {"type": "integer"}},
            },
        ),
        (
            FILE_URI,
            "",
            INNER_REF_WITH_NESTED_FILE_REF,
            {},
            {
                "properties": {"example": {"$ref": "#/x-moved-references/8c3ff8eb23370337fe1f4d50625776ca412cf3ce"}},
                "definitions": {"Example": {"$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719"}},
                "x-moved-references": {
                    "8c3ff8eb23370337fe1f4d50625776ca412cf3ce": {
                        "$ref": "#/x-moved-references/77c17a5efa18bdd0d75b1b8686d8daf4f881c719"
                    },
                    "77c17a5efa18bdd0d75b1b8686d8daf4f881c719": {"type": "integer"},
                },
            },
        ),
        (
            DEFAULT_URI,
            "",
            RECURSION_SCHEMA_ONE_HOP,
            {
                "definitions": {
                    "SchemaA": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "integer"},
                            # Points back to itself
                            "recursive": {"$ref": "#/definitions/SchemaA"},
                        },
                    }
                },
            },
            {
                "$ref": "#/x-moved-references/eebcedb296ce3a3a3e7ac8c3938de062de9ea618",
                "x-moved-references": {
                    "eebcedb296ce3a3a3e7ac8c3938de062de9ea618": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "integer"},
                            "recursive": {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}, "recursive": {}},
                            },
                        },
                    }
                },
            },
        ),
        (
            DEFAULT_URI,
            "",
            RECURSION_SCHEMA_ONE_HOP,
            {
                "definitions": {
                    "SchemaA": {
                        "anyOf": [
                            {"type": "integer"},
                            {"$ref": "#/definitions/SchemaA"},
                        ]
                    }
                },
            },
            {
                "$ref": "#/x-moved-references/eebcedb296ce3a3a3e7ac8c3938de062de9ea618",
                "x-moved-references": {
                    "eebcedb296ce3a3a3e7ac8c3938de062de9ea618": {
                        "anyOf": [
                            {"type": "integer"},
                            {"anyOf": [{"type": "integer"}, {}]},
                        ]
                    }
                },
            },
        ),
        (
            DEFAULT_URI,
            "",
            {"type": "integer", "nullable": True},
            {},
            {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
            },
        ),
    ),
    ids=(
        "boolean",
        "empty",
        "local-ref-no-nesting",
        "local-ref-nested-in-object",
        "local-nested-local-ref-nested-in-object",
        "local-ref-nested-in-object-multiple",
        "local-nested-ref-nested-in-object-multiple",
        "local-ref-nested-in-array",
        "local-ref-nested-in-array-multiple",
        "file-ref-no-nesting",
        "file-ref-with-scheme-no-nesting",
        "file-ref-nested-in-object",
        "file-nested-file-ref-nested-in-object",
        "file-nested-file-ref-nested-in-object-multiple",
        "file-relative-ref",
        "file-scoped-ref",
        "remote-ref-no-nesting",
        "remote-ref-nested-in-object",
        "remote-ref-nested-in-object-multiple",
        "inner-ref",
        "inner-ref-with-nested-file-ref",
        "recursive-one-hop",
        "recursive-one-hop-in-array",
        "with-nullable",
    ),
)
def test_to_jsonschema_valid(request, uri, scope, schema, components, expected):
    schema = fast_deepcopy(schema)
    components = fast_deepcopy(components)
    if isinstance(schema, dict):
        schema.update(components)

    uri, scope, schema = setup_schema(request, uri, scope, schema)
    uri = scope or uri
    retrieve = get_remote_schema_retriever(DRAFT4)
    registry = Registry(retrieve=retrieve).with_resource(uri, Resource(contents=schema, specification=DRAFT4))
    resolver = registry.resolver(base_uri=uri)
    config = TransformConfig(nullable_key="nullable", component_names=list(components))
    schema = to_jsonschema(schema, resolver, config)
    assert schema == expected

    # Hypothesis-jsonschema should be able to generate data for the inlined schema

    @given(from_schema(schema))
    @settings(
        deadline=None,
        database=None,
        max_examples=1,
        suppress_health_check=list(HealthCheck),
        phases=[Phase.explicit, Phase.generate],
        verbosity=Verbosity.quiet,
    )
    def generate(_):
        pass

    generate()
