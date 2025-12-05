import json
import platform
from pathlib import Path

import pytest
from flask import Flask, jsonify
from hypothesis import HealthCheck, given, settings
from jsonschema.validators import Draft4Validator
from werkzeug.exceptions import InternalServerError

import schemathesis
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Ok
from schemathesis.generation.modes import GenerationMode
from schemathesis.specs.openapi.stateful import dependencies

from .utils import as_param, get_schema_path, integer

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
    [
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
    ],
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
    ],
)
@pytest.mark.hypothesis_nested
@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="SIGSEGV on PyPy")
def test_drop_recursive_references_from_the_last_resolution_level(ctx, definition):
    raw_schema = ctx.openapi.build_schema({})
    build_schema_with_recursion(raw_schema, definition)
    schema = schemathesis.openapi.from_dict(raw_schema)

    validator = Draft4Validator({**USER_REFERENCE, "components": raw_schema["components"]})

    @given(case=schema["/users"]["POST"].as_strategy())
    @settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], deadline=None)
    def test(case):
        # Generated payload should be valid for the original schema (with references)
        try:
            validator.validate(case.body)
        except RecursionError:
            pass

    test()


@pytest.mark.parametrize(
    "definition",
    [
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
            "required": ["parent"],
        },
    ],
)
@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_non_removable_recursive_references(ctx, definition):
    schema = ctx.openapi.build_schema({})
    build_schema_with_recursion(schema, definition)
    schema = schemathesis.openapi.from_dict(schema)

    with pytest.raises(InvalidSchema):
        schema["/users"]["POST"]


def test_nested_recursive_references(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/folders": {
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
        },
        components={
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
        },
    )
    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/folders"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    test()


def test_simple_dereference(testdir):
    # When a given parameter contains a JSON reference
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_recursive_dereference(testdir):
    # When a given parameter contains a JSON reference, that reference an object with another reference
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_inner_dereference(testdir):
    # When a given parameter contains a JSON reference inside a property of an object
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_inner_dereference_with_lists(testdir):
    # When a given parameter contains a JSON reference inside a list in `allOf`
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


@pytest.mark.parametrize("extra", [{}, {"enum": ["foo"]}])
@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
def test_nullable_parameters(ctx, testdir, version, extra):
    schema = ctx.openapi.build_schema(
        {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}, version=version
    )
    if version == "2.0":
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_nullable_properties(testdir):
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-vv", "-s")
    result.assert_outcomes(passed=1)
    # At least one `None` value should be generated
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_nullable_ref(testdir):
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    if not hasattr(case.meta.phase.data, "description"):
        assert isinstance(case.body, int) or case.body is None
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 3$"])


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
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_nullable_enum(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "GET"
    if not hasattr(case.meta.phase.data, "description"):
        assert case.query["id"] in ("null", 1, 2)
""",
        **as_param(integer(name="id", required=True, enum=[1, 2], **{"x-nullable": True})),
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 4$"])


def test_complex_dereference(complex_schema):
    schema = schemathesis.openapi.from_path(complex_schema)
    body_definition = {
        "schema": {
            "$ref": "#/x-bundled/schema1",
            "x-bundled": {
                "schema1": {
                    "additionalProperties": False,
                    "description": "Test",
                    "properties": {"profile": {"$ref": "#/x-bundled/schema2"}, "username": {"type": "string"}},
                    "required": ["username", "profile"],
                    "type": "object",
                },
                "schema2": {
                    "additionalProperties": False,
                    "description": "Test",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                    "type": "object",
                },
            },
        }
    }
    operation = schema["/teapot"]["POST"]
    assert operation.base_url == "file:///"
    assert operation.path == "/teapot"
    assert operation.method == "post"
    assert len(operation.body) == 1
    assert operation.body[0].is_required
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


def test_unique_objects_after_inlining(ctx):
    # When the schema contains deep references
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/step5"}}},
                    },
                    "responses": {"default": {"description": "Success"}},
                }
            }
        },
        components={
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
        },
    )
    schema = schemathesis.openapi.from_dict(schema)
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


def test_unresolvable_reference_during_generation(ctx, testdir):
    # When there is a reference that can't be resolved during generation
    # Then it should be properly reported
    schema = ctx.openapi.build_schema(
        REFERENCE_TO_PARAM,
        components={
            "parameters": {"key": {"$ref": "#/components/schemas/Key0"}},
            "schemas": {
                # The last key does not point anywhere
                **{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(8)},
            },
        },
    )
    main = testdir.mkdir("root") / "main.json"
    main.write_text(json.dumps(schema), "utf8")
    schema = schemathesis.openapi.from_path(str(main))

    with pytest.raises(InvalidSchema, match="Unresolvable reference in the schema"):
        schema["/test"]["GET"].as_strategy()


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("Key7", "Invalid Schema Object definition for `Key7`"),
        ("Key8", "Invalid Schema Object definition for `Key8`"),
    ],
)
def test_uncommon_type_in_generation(ctx, testdir, key, expected):
    # When there is a reference that leads to a non-dictionary
    # Then it should not lead to an error
    schema = ctx.openapi.build_schema(
        REFERENCE_TO_PARAM,
        components={
            "parameters": {"key": {"$ref": "#/components/schemas/Key0"}},
            "schemas": {**{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(8)}, key: None},
        },
    )
    main = testdir.mkdir("root") / "main.json"
    main.write_text(json.dumps(schema), "utf8")
    schema = schemathesis.openapi.from_path(str(main))

    with pytest.raises(Exception, match=expected):

        @given(case=schema["/test"]["GET"].as_strategy())
        def test(case):
            pass

        test()


def test_global_security_schemes_with_custom_scope(ctx, testdir, cli, snapshot_cli, openapi3_base_url):
    # See GH-2300
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "$ref": "paths/tests/test.json",
            }
        },
        components={
            "securitySchemes": {
                "bearerAuth": {
                    "$ref": "components/securitySchemes/bearerAuth.json",
                }
            }
        },
        security=[{"bearerAuth": []}],
    )
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
    raw_schema_path.write_text(json.dumps(schema), "utf8")
    components = (root / "components").mkdir()
    paths = (root / "paths").mkdir()
    tests = (paths / "tests").mkdir()
    security_schemes = (components / "securitySchemes").mkdir()
    (security_schemes / "bearerAuth.json").write_text(json.dumps(bearer), "utf8")
    (tests / "test.json").write_text(json.dumps(operation), "utf8")

    assert (
        cli.run(
            str(raw_schema_path),
            f"--url={openapi3_base_url}",
            "--checks=not_a_server_error",
            "--mode=all",
            config={"warnings": False},
        )
        == snapshot_cli
    )


def test_missing_file_in_resolution(ctx, testdir, cli, snapshot_cli, openapi3_base_url):
    schema = ctx.openapi.build_schema({"/test": {"$ref": "paths/test.json"}})
    root = testdir.mkdir("root")
    raw_schema_path = root / "openapi.json"
    raw_schema_path.write_text(json.dumps(schema), "utf8")

    assert cli.run(str(raw_schema_path), f"--url={openapi3_base_url}") == snapshot_cli


def test_unresolvable_operation(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "$ref": "#/0",
                    "responses": {
                        "default": {
                            "description": "Ok",
                        }
                    },
                }
            }
        }
    )
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=fuzzing") == snapshot_cli


@pytest.mark.parametrize(
    ["paths", "components"],
    [
        (
            {
                "/changes": {
                    "post": {
                        "requestBody": {
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/issue_change"}}}
                        },
                        "responses": {"default": {"description": "Ok"}},
                    }
                }
            },
            {
                "schemas": {
                    "account": {"$ref": "#/components/schemas/object"},
                    "issue": {
                        "allOf": [
                            {"$ref": "#/components/schemas/object"},
                            {"properties": {"key": {"$ref": "#/components/schemas/account"}}},
                        ]
                    },
                    "issue_change": {
                        "properties": {"key": {"$ref": "#/components/schemas/issue"}},
                        "example": {"key": 42},
                    },
                    "object": {},
                }
            },
        ),
        (
            {
                "/changes": {
                    "post": {
                        "requestBody": {
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/issue_change"}}}
                        }
                    }
                }
            },
            {
                "schemas": {
                    "issue": {
                        "allOf": [
                            {"$ref": "#/components/schemas/object"},
                            {"properties": {"key": {"$ref": "#/components/schemas/milestone"}}},
                        ]
                    },
                    "issue_change": {
                        "properties": {"key": {"$ref": "#/components/schemas/issue"}},
                        "example": {"key": 42},
                    },
                    "milestone": {"allOf": [{"$ref": "#/components/schemas/object"}]},
                    "object": {},
                }
            },
        ),
    ],
)
@pytest.mark.filterwarnings("error")
def test_multiple_hops_references(ctx, cli, openapi3_base_url, snapshot_cli, paths, components):
    schema_path = ctx.openapi.write_schema(paths, components=components)
    # There should be no recursion error in another thread
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--phases=examples",
            "--checks=not_a_server_error",
            config={"warnings": False},
        )
        == snapshot_cli
    )


@pytest.mark.filterwarnings("error")
def test_multiple_hops_references_swagger(ctx, cli, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "put": {
                    "parameters": [
                        {
                            "in": "body",
                            "schema": {
                                "$ref": "#/definitions/A1",
                            },
                        }
                    ]
                }
            }
        },
        definitions={
            "A1": {
                "properties": {
                    "": {
                        "$ref": "#/definitions/A2",
                    }
                }
            },
            "A2": {
                "allOf": [
                    {"$ref": "#/definitions/allOf"},
                    {"$ref": "#/definitions/A3"},
                ]
            },
            "A3": {
                "properties": {
                    "key": {
                        "items": {"$ref": "#/definitions/A4"},
                    }
                }
            },
            "A4": {
                "properties": {
                    "key": {"$ref": "#/definitions/A5"},
                }
            },
            "A5": {"$ref": "#/definitions/allOf"},
            "allOf": {},
        },
        version="2.0",
    )
    # There should be no recursion error in another thread
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--phases=examples",
            "--checks=not_a_server_error",
            config={"warnings": False},
        )
        == snapshot_cli
    )


@pytest.mark.filterwarnings("error")
def test_responses_in_another_file(ctx, cli, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/api/v1/items": {
                "get": {
                    "responses": {
                        "200": {"description": "ОК"},
                        "400": {"$ref": "./schemas/responses.json#/BadRequest"},
                        "500": {"$ref": "./schemas/responses.json#/InternalServerError"},
                    }
                }
            }
        },
        version="3.1.0",
    )
    ctx.makefile(
        {
            "BadRequest": {"content": {"application/json": {"schema": {"$ref": "#/Error"}}}},
            "InternalServerError": {"content": {"application/json": {"schema": {"$ref": "#/Error"}}}},
            "Error": {"type": "object", "properties": {"message": {"type": "string"}}},
        },
        filename="responses",
        parent="schemas",
    )
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}") == snapshot_cli


def test_iter_when_ref_resolves_to_none_in_body(ctx):
    # Key0 -> Key1 -> Key2 -> Key3 (None)
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Key0"}}},
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                **{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(3)},
                "Key3": None,
            }
        },
    )

    schema = schemathesis.openapi.from_dict(schema)

    # Should not fail
    for _ in schema.get_all_operations():
        pass


def test_resolve_large_schema():
    path = get_schema_path("openapi3.json")
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"version": "1.0.0", "title": "My API", "description": "My HTTP interface."},
        "paths": {
            "/": {
                "get": {
                    "summary": "OpenAPI description (this document)",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/openapi+json": {
                                    "schema": {
                                        "$ref": Path(path).as_uri(),
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)

    # Should not fail
    for _ in schema.get_all_operations():
        pass


@pytest.mark.parametrize(
    "kind",
    ["html", "500", "number"],
    ids=["html_instead_of_yaml", "500", "number"],
)
def test_remote_ref_fails(ctx, kind, cli, snapshot_cli, app_runner):
    app = Flask(__name__)
    path = "/external/schemas/user.yaml"

    @app.route("/openapi.json")
    def openapi():
        return jsonify(
            ctx.openapi.build_schema(
                {
                    "/test": {
                        "get": {
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": f"http://127.0.0.1:{port}{path}#/User",
                                        }
                                    }
                                },
                                "required": True,
                            },
                        }
                    }
                }
            )
        )

    if kind == "html":

        @app.route(path)
        def external():
            html = "<!doctype html><html><title>Not YAML</title><body>Oops</body></html>"
            return html, 200, {"Content-Type": "text/html"}

    elif kind == "500":

        @app.route(path)
        def external():
            raise InternalServerError

    elif kind == "number":

        @app.route(path)
        def external():
            return jsonify(42)

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--checks=not_a_server_error",
            config={"warnings": False},
        )
        == snapshot_cli
    )


@pytest.mark.hypothesis_nested
def test_bundling_cache_with_shared_references(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/items": {
                "put": {
                    "parameters": [
                        {"in": "path", "name": "key", "type": "string"},
                        {"in": "body", "schema": {"$ref": "#/definitions/Connection"}},
                    ]
                }
            }
        },
        version="2.0",
        definitions={
            "Connection": {
                "allOf": [{"$ref": "#/definitions/Resource"}],
                "properties": {"key": {"properties": {"api": {"$ref": "#/definitions/ExpandedParent[ApiEntity]"}}}},
            },
            "ExpandedParent[ApiEntity]": {"$ref": "#/definitions/Resource"},
            "Resource": {},
        },
    )

    schema = schemathesis.openapi.from_dict(schema)
    operation = next(schema.get_all_operations()).ok()

    @given(case=operation.as_strategy())
    @settings(max_examples=3)
    def test(case):
        pass

    test()


@pytest.mark.hypothesis_nested
def test_bundling_cache_returns_independent_copies(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/path1": {
                "get": {
                    "parameters": [
                        {"in": "body", "schema": {"$ref": "#/definitions/Model"}},
                    ]
                }
            },
            "/path2": {
                "post": {
                    "parameters": [
                        {"in": "body", "schema": {"$ref": "#/definitions/Model"}},
                    ]
                }
            },
        },
        version="2.0",
        definitions={
            "Model": {"type": "object", "properties": {"id": {"type": "integer"}}},
        },
    )

    schema = schemathesis.openapi.from_dict(schema)
    ops = list(schema.get_all_operations())

    op1 = ops[0].ok()
    op2 = ops[1].ok()

    @given(case=op1.as_strategy())
    @settings(max_examples=1)
    def test1(case):
        pass

    @given(case=op2.as_strategy())
    @settings(max_examples=1)
    def test2(case):
        pass

    test1()
    test2()


def test_nested_external_refs_with_relative_paths(ctx):
    # See GH-3361
    schema_path = ctx.openapi.write_schema(
        {"/media": {"$ref": "media/feed.json#/paths/Feed"}},
        version="3.1.0",
    )

    # types.json - sibling to parameters.json
    ctx.makefile({"MetaPage": {"type": "integer", "minimum": 1, "maximum": 100}}, filename="types")

    # parameters.json - references types.json (sibling file)
    ctx.makefile(
        {"Page": {"name": "page", "in": "query", "schema": {"$ref": "types.json#/MetaPage"}}},
        filename="parameters",
    )

    # media/feed.json - references ../parameters.json (parent directory)
    ctx.makefile(
        {
            "paths": {
                "Feed": {
                    "get": {
                        "operationId": "media.feed",
                        "responses": {"200": {"description": "OK"}},
                        "parameters": [{"$ref": "../parameters.json#/Page"}],
                    }
                }
            }
        },
        filename="feed",
        parent="media",
    )

    schema = schemathesis.openapi.from_path(str(schema_path))

    # Should successfully iterate operations without reference resolution errors
    operations = list(schema.get_all_operations())
    assert len(operations) == 1

    result = operations[0]
    assert isinstance(result, Ok)

    operation = result.ok()
    assert operation.path == "/media"
    assert operation.method == "get"

    params = list(operation.iter_parameters())
    assert len(params) == 1
    assert params[0].name == "page"


def test_nested_external_refs_in_request_body(ctx):
    # Similar to GH-3361 but for requestBody in OpenAPI 3.x
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {"$ref": "requests/body.json#/CreateItem"},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )

    # types.json - in root, sibling to schema.json
    ctx.makefile(
        {"Item": {"type": "object", "properties": {"name": {"type": "string"}}}},
        filename="types",
    )

    # requests/body.json - references ../types.json
    ctx.makefile(
        {
            "CreateItem": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "../types.json#/Item"}}},
            }
        },
        filename="body",
        parent="requests",
    )

    schema = schemathesis.openapi.from_path(str(schema_path))

    operations = list(schema.get_all_operations())
    assert len(operations) == 1

    result = operations[0]
    assert isinstance(result, Ok)

    operation = result.ok()
    assert operation.path == "/items"
    assert operation.method == "post"
    assert len(operation.body) == 1


def test_nested_external_refs_in_response_for_stateful(ctx):
    # Response schemas with nested external refs should resolve correctly for stateful testing
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "get": {
                    "operationId": "getItems",
                    "responses": {"200": {"$ref": "responses/item.json#/ItemResponse"}},
                }
            }
        },
        version="3.1.0",
    )

    # types.json - in root
    ctx.makefile(
        {"Item": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}},
        filename="types",
    )

    # responses/item.json - references ../types.json
    ctx.makefile(
        {
            "ItemResponse": {
                "description": "Success",
                "content": {"application/json": {"schema": {"$ref": "../types.json#/Item"}}},
            }
        },
        filename="item",
        parent="responses",
    )

    schema = schemathesis.openapi.from_path(str(schema_path))

    graph = dependencies.analyze(schema)

    # Should find the Item resource from the response schema
    assert len(graph.resources) > 0 or len(graph.operations) > 0


def test_nested_external_refs_in_array_items_for_stateful(ctx):
    # Nested $refs inside array items should resolve correctly for stateful testing
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "get": {
                    "operationId": "getItems",
                    "responses": {"200": {"$ref": "responses/list.json#/ListResponse"}},
                }
            }
        },
        version="3.1.0",
    )

    # types.json - in root
    ctx.makefile(
        {"Item": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}},
        filename="types",
    )

    # responses/list.json - has array with items referencing ../types.json
    ctx.makefile(
        {
            "ListResponse": {
                "description": "Success",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"items": {"type": "array", "items": {"$ref": "../types.json#/Item"}}},
                        }
                    }
                },
            }
        },
        filename="list",
        parent="responses",
    )

    schema = schemathesis.openapi.from_path(str(schema_path))

    graph = dependencies.analyze(schema)

    # Should find resources - the array items ref should resolve correctly
    assert len(graph.resources) > 0 or len(graph.operations) > 0


@pytest.mark.hypothesis_nested
def test_prefix_items_with_ref(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/v1/customers/": {
                "patch": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CustomerUpdate"}}},
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "CustomerUpdate": {
                    "properties": {
                        "key": {
                            "anyOf": [
                                {
                                    "prefixItems": [{"$ref": "#/components/schemas/TaxIDFormat"}],
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "type": "array",
                                },
                                {"type": "null"},
                            ]
                        }
                    },
                    "type": "object",
                },
                "TaxIDFormat": {"type": "string"},
            }
        },
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/v1/customers/"]["PATCH"].as_strategy())
    @settings(max_examples=50)
    def test(case):
        key = case.body.get("key")
        if key is not None:
            assert isinstance(key, list)
            assert len(key) == 2
            assert isinstance(key[0], str)

    test()
