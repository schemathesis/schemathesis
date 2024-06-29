import json
import platform
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError
from jsonschema.validators import Draft4Validator

import schemathesis
from schemathesis.exceptions import SchemaError

from .utils import as_param, get_schema, integer


@pytest.fixture()
def petstore():
    return get_schema("petstore_v2.yaml")


@pytest.mark.parametrize(
    "ref, expected",
    (
        (
            {"$ref": "#/definitions/Category"},
            {
                "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                "type": "object",
                "xml": {"name": "Category"},
            },
        ),
        (
            {"$ref": "#/definitions/Pet"},
            {
                "properties": {
                    "category": {
                        "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                        "type": "object",
                        "xml": {"name": "Category"},
                    },
                    "id": {"format": "int64", "type": "integer"},
                    "name": {"example": "doggie", "type": "string"},
                    "photoUrls": {
                        "items": {"type": "string"},
                        "type": "array",
                        "xml": {"name": "photoUrl", "wrapped": True},
                        "example": ["https://photourl.com"],
                    },
                    "status": {
                        "description": "pet status in the store",
                        "enum": ["available", "pending", "sold"],
                        "type": "string",
                    },
                    "tags": {
                        "items": {
                            "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                            "type": "object",
                            "xml": {"name": "Tag"},
                        },
                        "type": "array",
                        "xml": {"name": "tag", "wrapped": True},
                    },
                },
                "required": ["name", "photoUrls"],
                "type": "object",
                "xml": {"name": "Pet"},
            },
        ),
    ),
)
def test_resolve(petstore, ref, expected):
    assert petstore.resolver.resolve_all(ref) == expected


def test_recursive_reference(mocker, schema_with_recursive_references):
    mocker.patch("schemathesis.specs.openapi.references.RECURSION_DEPTH_LIMIT", 1)
    reference = {"$ref": "#/components/schemas/Node"}
    schema = schemathesis.from_dict(schema_with_recursive_references)
    assert schema.resolver.resolve_all(reference) == {
        "properties": {"child": {"properties": {"child": reference}, "required": ["child"], "type": "object"}},
        "required": ["child"],
        "type": "object",
    }


USER_REFERENCE = {"$ref": "#/components/schemas/User"}
ELIDABLE_SCHEMA = {"description": "Test", "type": "object", "properties": {"foo": {"type": "integer"}}}
ALL_OF_ROOT = {"allOf": [USER_REFERENCE, {"description": "Test"}], "type": "object", "additionalProperties": False}


def build_schema_with_recursion(schema, definition):
    schema["paths"]["/users"] = {
        "post": {
            "description": "Test",
            "summary": "Test",
            "requestBody": {"content": {"application/json": {"schema": USER_REFERENCE}}, "required": True},
            "responses": {"200": {"description": "Test"}},
        }
    }
    schema["components"] = {"schemas": {"User": definition}}


@pytest.mark.parametrize(
    "definition",
    (
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"parent": USER_REFERENCE, "foo": {"type": "integer"}},
        },
        {"type": "array", "items": USER_REFERENCE, "maxItems": 1},
        {"type": "array", "items": [USER_REFERENCE, {"type": "integer"}], "maxItems": 1},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "children": {
                    "items": USER_REFERENCE,
                    "maxItems": 1,
                    "type": "array",
                },
            },
        },
        {"type": "object", "additionalProperties": USER_REFERENCE, "maxProperties": 1},
        {"type": "object", "additionalProperties": False, "properties": {"parent": {"allOf": [USER_REFERENCE]}}},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"parent": {"allOf": [USER_REFERENCE, {"description": "Test"}]}},
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"parent": {"allOf": [USER_REFERENCE, ELIDABLE_SCHEMA]}},
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"parent": {"allOf": [ELIDABLE_SCHEMA, USER_REFERENCE]}},
        },
        {"type": "array", "items": {"allOf": [USER_REFERENCE]}, "maxItems": 1},
        ALL_OF_ROOT,
    ),
    ids=[
        "properties",
        "items-object",
        "items-array",
        "items-inside-properties",
        "additionalProperties",
        "allOf-one-item-properties",
        "allOf-one-item-properties-with-empty-schema",
        "allOf-one-item-properties-with-elidable-schema-1",
        "allOf-one-item-properties-with-elidable-schema-2",
        "allOf-one-item-items",
        "allOf-one-item-root",
    ],
)
@pytest.mark.hypothesis_nested
@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_drop_recursive_references_from_the_last_resolution_level(empty_open_api_3_schema, definition):
    build_schema_with_recursion(empty_open_api_3_schema, definition)
    schema = schemathesis.from_dict(empty_open_api_3_schema)

    validator = Draft4Validator({**USER_REFERENCE, "components": empty_open_api_3_schema["components"]})

    @given(case=schema["/users"]["POST"].as_strategy())
    @settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], deadline=None)
    def test(case):
        # Generated payload should be valid for the original schema (with references)
        try:
            validator.validate(case.body)
        except RecursionError:
            # jsonschema infinitely recurse on some cases
            if definition is not ALL_OF_ROOT:
                raise
            pass

    test()


@pytest.mark.parametrize(
    "definition",
    (
        USER_REFERENCE,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["parent"],
            "properties": {"parent": USER_REFERENCE, "foo": {"type": "integer"}},
        },
        {"type": "array", "items": USER_REFERENCE, "minItems": 1},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "parent": {
                    "allOf": [
                        {"type": "object", "properties": {"foo": {"type": "integer"}}, "required": ["foo"]},
                        USER_REFERENCE,
                    ]
                }
            },
        },
    ),
)
@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_non_removable_recursive_references(empty_open_api_3_schema, definition):
    build_schema_with_recursion(empty_open_api_3_schema, definition)
    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/users"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    with pytest.raises(HypothesisRefResolutionError):
        test()


def test_nested_recursive_references(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"]["/folders"] = {
        "post": {
            "description": "Test",
            "summary": "Test",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": "#/components/schemas/editFolder",
                        }
                    }
                },
                "required": True,
            },
            "responses": {"200": {"description": "Test"}},
        }
    }
    empty_open_api_3_schema["components"] = {
        "schemas": {
            "editFolder": {
                "type": "object",
                "properties": {
                    "parent": {"$ref": "#/components/schemas/Folder"},
                },
                "additionalProperties": False,
            },
            "Folder": {
                "type": "object",
                "properties": {
                    "folders": {"$ref": "#/components/schemas/Folders"},
                },
                "additionalProperties": False,
            },
            "Folders": {
                "type": "object",
                "properties": {
                    "folder": {
                        "allOf": [
                            {
                                "minItems": 1,
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Folder"},
                            }
                        ]
                    },
                },
                "additionalProperties": False,
            },
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema, validate_schema=True)

    @given(case=schema["/folders"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    test()


def test_simple_dereference(testdir):
    # When a given parameter contains a JSON reference
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    assert_int(case.body)
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "#/definitions/SimpleIntRef"},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_recursive_dereference(testdir):
    # When a given parameter contains a JSON reference, that reference an object with another reference
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    assert_int(case.body["id"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "#/definitions/ObjectRef"},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={
            "ObjectRef": {
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
            },
            "SimpleIntRef": {"type": "integer"},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_inner_dereference(testdir):
    # When a given parameter contains a JSON reference inside a property of an object
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    assert_int(case.body["id"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
                            },
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_inner_dereference_with_lists(testdir):
    # When a given parameter contains a JSON reference inside a list in `allOf`
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    assert_int(case.body["id"]["a"])
    assert_str(case.body["id"]["b"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {
                                    "id": {"allOf": [{"$ref": "#/definitions/A"}, {"$ref": "#/definitions/B"}]}
                                },
                            },
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={
            "A": {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
            "B": {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


@pytest.mark.parametrize("extra", ({}, {"enum": ["foo"]}))
@pytest.mark.parametrize("spec_version", ("open_api_2", "open_api_3"))
def test_nullable_parameters(request, testdir, spec_version, extra):
    schema = request.getfixturevalue(f"empty_{spec_version}_schema")
    schema["paths"] = {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}
    if spec_version == "open_api_2":
        schema["paths"]["/users"]["get"]["parameters"] = [
            {"in": "query", "name": "id", "type": "string", "x-nullable": True, "required": True, **extra}
        ]
    else:
        schema["paths"]["/users"]["get"]["parameters"] = [
            {"in": "query", "name": "id", "schema": {"type": "string", "nullable": True, **extra}, "required": True}
        ]
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    assume(case.query["id"] == "null")
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "GET"
""",
        schema=schema,
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_properties(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    assume(case.body["id"] is None)
    assert case.path == "/users"
    assert case.method == "POST"
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "attributes",
                            "schema": {
                                "type": "object",
                                "properties": {"id": {"type": "integer", "format": "int64", "x-nullable": True}},
                                "required": ["id"],
                            },
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-vv", "-s")
    result.assert_outcomes(passed=1)
    # At least one `None` value should be generated
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_ref(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    assert case.body is None
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "attributes",
                            "schema": {"$ref": "#/definitions/NullableIntRef"},
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={"NullableIntRef": {"type": "integer", "x-nullable": True}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_path_ref(testdir):
    # When path is specified via `$ref`
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert isinstance(case.body, str)
""",
        paths={"/users": {"$ref": "#/x-paths/UsersPath"}},
        **{
            # custom extension `x-paths` to be compliant with the spec, otherwise there is no handy place
            # to put the referenced object
            "x-paths": {
                "UsersPath": {
                    "post": {
                        "parameters": [{"schema": {"type": "string"}, "in": "body", "name": "object", "required": True}]
                    }
                }
            }
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_enum(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "GET"
    assert case.query["id"] == "null"
""",
        **as_param(integer(name="id", required=True, enum=[1, 2], **{"x-nullable": True})),
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_complex_dereference(testdir, complex_schema):
    schema = schemathesis.from_path(complex_schema)
    path = Path(str(testdir))
    body_definition = {
        "schema": {
            "additionalProperties": False,
            "description": "Test",
            "properties": {
                "profile": {
                    "additionalProperties": False,
                    "description": "Test",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                    "type": "object",
                },
                "username": {"type": "string"},
            },
            "required": ["username", "profile"],
            "type": "object",
        }
    }
    operation = schema["/teapot"]["POST"]
    assert operation.base_url == "file:///"
    assert operation.path == "/teapot"
    assert operation.method == "post"
    assert len(operation.body) == 1
    assert operation.body[0].required
    assert operation.body[0].media_type == "application/json"
    assert operation.body[0].definition == body_definition
    assert operation.definition.raw == {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "../schemas/teapot/create.yaml#/TeapotCreateRequest"}}},
            "description": "Test.",
            "required": True,
        },
        "responses": {"default": {"$ref": "../../common/responses.yaml#/DefaultError"}},
        "summary": "Test",
        "tags": ["ancillaries"],
    }
    assert operation.definition.resolved == {
        "requestBody": {
            "content": {"application/json": body_definition},
            "description": "Test.",
            "required": True,
        },
        "responses": {
            "default": {
                "content": {
                    "application/json": {
                        "schema": {
                            "additionalProperties": False,
                            "properties": {
                                # Note, these `nullable` keywords are not transformed at this point
                                # It is done during the response validation.
                                "key": {"type": "string", "nullable": True},
                                "referenced": {"type": "string", "nullable": True},
                            },
                            "required": ["key", "referenced"],
                            "type": "object",
                        }
                    }
                },
                "description": "Probably an error",
            }
        },
        "summary": "Test",
        "tags": ["ancillaries"],
    }
    assert operation.definition.scope == f"{path.as_uri()}/root/paths/teapot.yaml#/TeapotCreatePath"
    assert operation.body[0].required
    assert operation.body[0].media_type == "application/json"
    assert operation.body[0].definition == body_definition


def test_remote_reference_to_yaml(swagger_20, schema_url):
    scope, resolved = swagger_20.resolver.resolve(f"{schema_url}#/info/title")
    assert scope.endswith("#/info/title")
    assert resolved == "Example API"


def assert_unique_objects(item):
    seen = set()

    def check_seen(it):
        if id(it) in seen:
            raise ValueError(f"Seen: {it!r}")
        seen.add(id(it))

    def traverse(it):
        if isinstance(it, dict):
            check_seen(it)
            for value in it.values():
                traverse(value)
        if isinstance(it, list):
            check_seen(it)
            for value in it:
                traverse(value)

    traverse(item)


def test_unique_objects_after_inlining(empty_open_api_3_schema):
    # When the schema contains deep references
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/step5"}}},
                },
                "responses": {"default": {"description": "Success"}},
            }
        }
    }
    empty_open_api_3_schema["components"] = {
        "schemas": {
            "final": {"type": "object"},
            "step1": {"$ref": "#/components/schemas/final"},
            "step2": {"$ref": "#/components/schemas/step1"},
            "step3": {"$ref": "#/components/schemas/step2"},
            "step4": {"$ref": "#/components/schemas/step3"},
            "step5": {
                "properties": {
                    "first": {"$ref": "#/components/schemas/step4"},
                    "second": {"$ref": "#/components/schemas/step4"},
                }
            },
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    # Then inlined objects should be unique
    assert_unique_objects(schema["/test"]["post"].body[0].definition)


REFERENCE_TO_PARAM = {
    "/test": {
        "get": {
            "parameters": [
                {
                    "schema": {"$ref": "#/components/parameters/key"},
                    "in": "query",
                    "name": "key",
                    "required": True,
                }
            ],
            "responses": {"default": {"description": "Success"}},
        }
    }
}


def test_unresolvable_reference_during_generation(empty_open_api_3_schema, testdir):
    # When there is a reference that can't be resolved during generation
    # Then it should be properly reported
    empty_open_api_3_schema["paths"] = REFERENCE_TO_PARAM
    empty_open_api_3_schema["components"] = {
        "parameters": {"key": {"$ref": "#/components/schemas/Key0"}},
        "schemas": {
            # The last key does not point anywhere
            **{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(8)},
        },
    }
    main = testdir.mkdir("root") / "main.json"
    main.write_text(json.dumps(empty_open_api_3_schema), "utf8")
    schema = schemathesis.from_path(str(main))

    @given(case=schema["/test"]["GET"].as_strategy())
    def test(case):
        pass

    with pytest.raises(SchemaError, match="Unresolvable JSON pointer in the schema: /components/schemas/Key8"):
        test()


@pytest.mark.parametrize(
    "key, expected",
    (
        ("Key7", 'Can not generate data for query parameter "key"! Its schema should be an object, got None'),
        ("Key8", "Unresolvable JSON pointer: 'components/schemas/Key8'"),
    ),
)
def test_uncommon_type_in_generation(empty_open_api_3_schema, testdir, key, expected):
    # When there is a reference that leads to a non-dictionary
    # Then it should not lead to an error
    empty_open_api_3_schema["paths"] = REFERENCE_TO_PARAM
    empty_open_api_3_schema["components"] = {
        "parameters": {"key": {"$ref": "#/components/schemas/Key0"}},
        "schemas": {**{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(8)}, key: None},
    }
    main = testdir.mkdir("root") / "main.json"
    main.write_text(json.dumps(empty_open_api_3_schema), "utf8")
    schema = schemathesis.from_path(str(main))

    @given(case=schema["/test"]["GET"].as_strategy())
    def test(case):
        pass

    with pytest.raises(Exception, match=expected):
        test()


def test_global_security_schemes_with_custom_scope(testdir, empty_open_api_3_schema, cli, snapshot_cli):
    # See GH-2300
    empty_open_api_3_schema["components"] = {
        "securitySchemes": {
            "bearerAuth": {
                "$ref": "components/securitySchemes/bearerAuth.json",
            }
        }
    }
    empty_open_api_3_schema["security"] = [{"bearerAuth": []}]
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "$ref": "paths/tests/test.json",
        }
    }
    bearer = {"type": "http", "scheme": "bearer"}
    operation = {
        "get": {
            "description": "Test",
            "operationId": "test",
            "responses": {"200": {"description": "OK"}},
        }
    }
    root = testdir.mkdir("root")
    raw_schema_path = root / "openapi.json"
    raw_schema_path.write_text(json.dumps(empty_open_api_3_schema), "utf8")
    components = (root / "components").mkdir()
    paths = (root / "paths").mkdir()
    tests = (paths / "tests").mkdir()
    security_schemes = (components / "securitySchemes").mkdir()
    (security_schemes / "bearerAuth.json").write_text(json.dumps(bearer), "utf8")
    (tests / "test.json").write_text(json.dumps(operation), "utf8")

    assert cli.run(str(raw_schema_path), "--dry-run", "--show-trace") == snapshot_cli


def test_missing_file_in_resolution(testdir, empty_open_api_3_schema, cli, snapshot_cli):
    empty_open_api_3_schema["paths"] = {"/test": {"$ref": "paths/test.json"}}
    root = testdir.mkdir("root")
    raw_schema_path = root / "openapi.json"
    raw_schema_path.write_text(json.dumps(empty_open_api_3_schema), "utf8")

    assert cli.run(str(raw_schema_path), "--dry-run", "--show-trace") == snapshot_cli
