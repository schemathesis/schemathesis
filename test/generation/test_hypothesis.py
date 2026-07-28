import datetime
import re
import sys
import uuid
from base64 import b64decode

import jsonschema
import jsonschema_rs
import pytest
from hypothesis import HealthCheck, Phase, assume, find, given, settings
from hypothesis import strategies as st
from hypothesis.database import InMemoryExampleDatabase
from hypothesis.errors import InvalidArgument, Unsatisfiable
from hypothesis.internal.observability import with_observability_callback
from hypothesis_jsonschema import _canonicalise as canonicalise
from hypothesis_jsonschema import from_schema

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, FANCY_REGEX_OPTIONS
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.hypothesis import examples, setup
from schemathesis.generation.jsonschema import strategy
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase
from schemathesis.generation.modes import GenerationMode
from schemathesis.schemas import APIOperation, OperationDefinition, PayloadAlternatives
from schemathesis.specs.openapi._hypothesis import _canonical_strategy_or_none, jsonify_python_specific_types
from schemathesis.specs.openapi.adapter import v2
from schemathesis.specs.openapi.adapter.parameters import (
    OpenApiBody,
    OpenApiParameter,
    OpenApiParameterSet,
    form_data_to_json_schema,
)
from schemathesis.transport.serialization import Binary, quote_all
from test.utils import assert_requests_call


def make_operation(schema, **kwargs) -> APIOperation:
    return APIOperation(
        "/users",
        "POST",
        definition=OperationDefinition({}),
        schema=schema,
        responses=schema._parse_responses({}, ""),
        security=schema._parse_security({}),
        **kwargs,
    )


def test_canonicalish_keeps_bundle_when_bundled_ref_present():
    setup()
    schema = {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
        "type": "integer",
        "minimum": 1,
        "maximum": 1,
        BUNDLE_STORAGE_KEY: {"schema1": {"type": "integer"}},
    }

    assert canonicalise.canonicalish(schema) == {"const": 1, BUNDLE_STORAGE_KEY: schema[BUNDLE_STORAGE_KEY]}


def test_from_schema_reflects_bundle_mutations():
    setup()
    schema = {
        "$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1",
        BUNDLE_STORAGE_KEY: {"schema1": {"type": "integer"}},
    }

    assert isinstance(find(from_schema(schema), lambda _: True), int)

    schema[BUNDLE_STORAGE_KEY]["schema1"] = {"type": "string"}

    assert isinstance(find(from_schema(schema), lambda _: True), str)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({}, {"type": "integer", "minimum": 1, "maximum": 1}),
        ({"type": "integer", "minimum": 1, "maximum": 1}, {}),
        (True, {"type": "integer", "minimum": 1, "maximum": 1}),
        ({"type": "integer", "minimum": 1, "maximum": 1}, True),
    ],
    ids=["empty-left", "empty-right", "true-left", "true-right"],
)
def test_merged_truthy_identity(left, right):
    setup()

    expected = canonicalise.canonicalish(right if left in ({}, True) else left)

    assert canonicalise.merged([left, right]) == expected


@pytest.mark.parametrize(
    ("left", "right", "mutation_path"),
    [
        (
            {"type": "object", "properties": {"a": {"type": "integer"}}},
            {"required": ["a"]},
            ("properties", "a", "type"),
        ),
        (
            {"type": "array", "items": {"type": "integer"}},
            {"minItems": 1},
            ("items", "type"),
        ),
    ],
    ids=["object-property", "array-items"],
)
def test_merged_cache_returns_fresh_copy(left, right, mutation_path):
    setup()

    first = canonicalise.merged([left, right])
    assert isinstance(first, dict)

    target = first
    for key in mutation_path[:-1]:
        target = target[key]
    target[mutation_path[-1]] = "string"

    second = canonicalise.merged([left, right])

    assert second != first


def test_ref_with_sibling_anyof_against_anyof_target(ctx):
    body = {
        "type": "object",
        "properties": {
            "loadStrategyClass": {
                "$ref": "#/components/schemas/Strategy",
                "anyOf": [
                    {"const": "ai.starlake.IngestionNameStrategy"},
                    {"const": "ai.starlake.IngestionTimeStrategy"},
                ],
            }
        },
    }
    components = {
        "schemas": {
            "Strategy": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "boolean"},
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "null"},
                ],
            }
        }
    }
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
        components=components,
    )
    validator = jsonschema_rs.validator_for({**body, "components": components})

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=1, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        validator.validate(case.body)

    test()


def test_draft4_typed_integer_enum_stays_in_enum(ctx):
    # OpenAPI 3.0 is Draft 4, where an integer enum canonicalizes to a typed group; body must stay in the enum.
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "integer", "enum": [1, 2]}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body in (1, 2), case.body

    test()


def test_empty_enum_body_is_omitted(ctx):
    # An empty enum canonicalizes to `false`; an optional body carrying it generates no value.
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": False,
                        "content": {"application/json": {"schema": {"enum": []}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=5, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body is NOT_SET

    test()


def test_multitype_null_boolean_body(ctx):
    # A 3.1 `type: [null, boolean]` body lifts to a multi-type union; values stay null or boolean.
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": ["null", "boolean"]}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body is None or isinstance(case.body, bool), case.body

    test()


@pytest.mark.parametrize(
    "body_schema",
    [{"type": "string"}, {"type": "string", "pattern": ".+"}],
    ids=["plain", "pattern"],
)
@pytest.mark.parametrize("codec", ["utf-8", "ascii", None])
@pytest.mark.parametrize("allow_x00", [True, False])
def test_string_body_respects_alphabet(ctx, codec, allow_x00, body_schema):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.generation.allow_x00 = allow_x00
    schema.config.generation.codec = codec

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert isinstance(case.body, str)
        if not allow_x00:
            assert "\x00" not in case.body
        if codec is not None:
            case.body.encode(codec)

    test()


def test_anyof_disjoint_branches_body(ctx):
    # A 3.1 `anyOf` of branches that don't merge into one type lifts to a union; values stay in one branch.
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body is None or isinstance(case.body, int), case.body

    test()


@pytest.mark.parametrize(
    ("schema", "expected_module"),
    [
        ({"type": "string", "pattern": r"([\u0009-\u00FF]){1,51200}"}, "jsonschema_rs"),
        ({"type": "string", "pattern": r"[\uD800-\uDBFF]"}, "jsonschema.validators"),
        ({"type": "array", "items": [{"type": "string"}]}, "jsonschema_rs"),
    ],
    ids=["large-quantifier-rust", "surrogate-range-python-fallback", "tuple-items-rust-draft7"],
)
def test_make_validator_regex_backend_selection(schema, expected_module):
    setup()

    validator = canonicalise.make_validator(schema)

    assert validator._validator.__class__.__module__.startswith(expected_module)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": 1},
        {"type": "object", "properties": []},
    ],
    ids=["invalid-type-keyword", "invalid-properties-keyword"],
)
def test_get_validator_class_does_not_downgrade_non_regex_schema_errors(schema):
    setup()

    with pytest.raises(jsonschema_rs.ValidationError):
        canonicalise._get_validator_class(schema)


def test_get_validator_class_falls_back_to_older_drafts_for_tuple_items():
    setup()

    schema = {"type": "array", "items": [{"type": "string"}]}

    assert canonicalise._get_validator_class(schema) is jsonschema_rs.Draft7Validator


def test_draft_03_raises_invalid_schema(ctx):
    body = {
        "$schema": "http://json-schema.org/draft-03/schema#",
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=1, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        pass

    with pytest.raises(InvalidSchema, match="Draft-03"):
        test()


def test_canonicalise_constants_restored_after_polluting_schema(ctx):
    # hypothesis-jsonschema's FALSEY/TRUTHY are shared mutable globals that get
    # clobbered during generation; schemathesis must restore them.
    setup()
    schema = ctx.openapi.load_schema(
        {
            "/probe": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "routes": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/staticRoutes"},
                                        }
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "staticRoutes": {
                    "type": "object",
                    "if": {"properties": {"type": {"const": "uri"}}},
                    "then": {"properties": {"source": {"type": "string"}}, "required": ["source"]},
                    "else": {"properties": {"content": {"type": "string"}}, "required": ["content"]},
                    "required": ["route", "type"],
                    "additionalProperties": False,
                }
            }
        },
    )

    @given(schema["/probe"]["POST"].as_strategy())
    @settings(max_examples=1, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        pass

    test()

    assert canonicalise.FALSEY == {"not": {}}
    assert canonicalise.TRUTHY == {}


@pytest.mark.parametrize("location", sorted(set(ParameterLocation) - {ParameterLocation.UNKNOWN}))
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_get_examples(location, swagger_20):
    if location == ParameterLocation.BODY:
        # In Open API 2.0, the `body` parameter has a name, which is ignored
        # But we'd like to use this object as a payload; therefore, we put one extra level of nesting
        example = expected = {"name": "John"}
        media_type = "application/json"
        cls = PayloadAlternatives
        parameter_cls = OpenApiBody
        kwargs = {"media_type": media_type, "resource_name": None, "is_required": True}
        definition = {
            "in": location,
            "name": "name",
            "required": True,
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "x-example": example,
        }
    else:
        example = "John"
        expected = {"name": example}
        media_type = None  # there is no payload
        cls = OpenApiParameterSet
        parameter_cls = OpenApiParameter
        kwargs = {}
        definition = {
            "in": location,
            "name": "name",
            "required": True,
            "type": "string",
            "x-example": example,
        }
    container = location.container_name
    if location == ParameterLocation.BODY:
        param_set = cls([parameter_cls.from_definition(definition=definition, adapter=v2, name_to_uri={}, **kwargs)])
    else:
        param_set = cls(
            location,
            [parameter_cls.from_definition(definition=definition, adapter=v2, name_to_uri={}, **kwargs)],
            adapter=v2,
        )
    operation = make_operation(
        swagger_20,
        **{container: param_set},
    )
    strategies = operation.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example() == operation.Case(
        media_type=media_type,
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.FUZZING,
                data=FuzzingPhaseData(
                    description="",
                    parameter=None,
                    parameter_location=None,
                    location=None,
                ),
            ),
        ),
        **{container: expected},
    )


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_no_body_in_get(swagger_20):
    operation = APIOperation(
        path="/api/success",
        method="GET",
        definition=OperationDefinition({}),
        schema=swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
        query=OpenApiParameterSet(
            ParameterLocation.QUERY,
            [
                OpenApiParameter.from_definition(
                    definition={
                        "required": True,
                        "in": "query",
                        "type": "string",
                        "name": "key",
                        "x-example": "John",
                    },
                    name_to_uri={},
                    adapter=v2,
                )
            ],
            adapter=v2,
        ),
    )
    strategies = operation.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example().body is NOT_SET


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_strategies(swagger_20):
    schemathesis.openapi.format("even_4_digits", st.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    operation = make_operation(
        swagger_20,
        query=OpenApiParameterSet(
            ParameterLocation.QUERY,
            [
                OpenApiParameter.from_definition(
                    definition={
                        "name": "id",
                        "in": "query",
                        "required": True,
                        "type": "string",
                        "format": "even_4_digits",
                    },
                    name_to_uri={},
                    adapter=v2,
                )
            ],
            adapter=v2,
        ),
    )
    result = operation.as_strategy().example()
    assert len(result.query["id"]) == 4
    assert int(result.query["id"]) % 2 == 0


def test_default_strategies_binary(swagger_20):
    body = OpenApiBody.from_form_parameters(
        definition=form_data_to_json_schema(
            [
                {
                    "name": "upfile",
                    "in": "formData",
                    "type": "file",
                    "required": True,
                }
            ]
        ),
        name_to_uri={},
        media_type="multipart/form-data",
        adapter=v2,
    )
    operation = make_operation(swagger_20, body=PayloadAlternatives([body]))
    swagger_20.raw_schema["consumes"] = ["multipart/form-data"]
    case = examples.generate_one(operation.as_strategy())
    assert isinstance(case.body["upfile"], Binary)
    kwargs = case.as_transport_kwargs(base_url="http://127.0.0.1")
    assert kwargs["files"] == [("upfile", case.body["upfile"])]


def test_merge_length_into_pattern(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "string",
                                    # Unlikely to generate a string of this length from a pattern
                                    "minLength": 460,
                                    "maxLength": 465,
                                    "pattern": "^[a-z]+$",
                                },
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    operation = schema["/data"]["POST"]

    @given(operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    test()


def test_required_without_properties(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "additionalProperties": False,
                                    "type": "object",
                                    "required": ["A"],
                                },
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    operation = schema["/data"]["POST"]

    @given(operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    test()


def test_non_schema_property_value(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "maxItems": 0,
                                    },
                                },
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    operation = schema["/data"]["POST"]

    @given(operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    test()


def test_as_strategy_example_resolves_bundled_refs(tmp_path):
    # The public Python API path must work without prior CLI/pytest imports.
    import subprocess
    import sys
    import textwrap

    script = tmp_path / "probe.py"
    script.write_text(
        textwrap.dedent("""
        import schemathesis

        schema = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0.0"},
            "paths": {
                "/probe": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "outer": {
                                                "$ref": "#/components/schemas/Outer",
                                                "properties": {
                                                    "inner": {"$ref": "#/components/schemas/Inner"}
                                                },
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "Outer": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                    },
                    "Inner": {"type": "object", "properties": {"flag": {"type": "boolean"}}},
                }
            },
        }
        api = schemathesis.openapi.from_dict(schema)
        case = api["/probe"]["POST"].as_strategy().example()
        assert case is not None
    """)
    )
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_invalid_schema_for_malformed_subschema(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/probe": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {"field": {"$ref": "#/components/schemas/Bad"}},
                                },
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={"schemas": {"Bad": {"type": "string", "maxLength": "5"}}},
    )

    with pytest.raises(InvalidSchema, match="maxLength"):
        examples.generate_one(schema["/probe"]["POST"].as_strategy())


def test_malformed_annotation_behind_a_reference_still_generates(ctx):
    # An annotation cannot change which values the schema admits, and canonicalization drops it.
    schema = ctx.openapi.load_schema(
        {
            "/probe": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"field": {"$ref": "#/components/schemas/Annotated"}},
                                    "required": ["field"],
                                },
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={"schemas": {"Annotated": {"type": "string", "description": None}}},
    )

    case = examples.generate_one(schema["/probe"]["POST"].as_strategy())

    assert isinstance(case.body["field"], str)


@pytest.mark.parametrize("version", ["3.0.0", "3.1.0"])
def test_array_with_allof_of_multiple_contains(ctx, version):
    # Filtering for several `contains` demands exhausts before both consts land, so each is placed:
    # by the `contains` strategy under 3.1, by the converter's positional prefix under Draft 4.
    schema = ctx.openapi.load_schema(
        {
            "/probe": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["type"],
                                    "properties": {
                                        "type": {
                                            "type": "array",
                                            "minItems": 1,
                                            "uniqueItems": True,
                                            "items": {"type": "string"},
                                            "allOf": [
                                                {"contains": {"const": "VerifiablePresentation"}},
                                                {"contains": {"const": "KyaManifest"}},
                                            ],
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )

    case = examples.generate_one(schema["/probe"]["POST"].as_strategy())

    assert "VerifiablePresentation" in case.body["type"]
    assert "KyaManifest" in case.body["type"]


@pytest.mark.parametrize("media_type", ["application/json", "text/yaml"])
def test_binary_is_serializable(ctx, media_type):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    operation = schema["/data"]["POST"]

    @given(operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        assert_requests_call(case)
        assert case.as_transport_kwargs()["data"] == case.body.data

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_bytes(swagger_20):
    operation = make_operation(
        swagger_20,
        body=PayloadAlternatives(
            [
                OpenApiBody.from_definition(
                    definition={
                        "in": "body",
                        "name": "byte",
                        "required": True,
                        "schema": {"type": "string", "format": "byte"},
                    },
                    is_required=True,
                    media_type="text/plain",
                    name_to_uri={},
                    resource_name=None,
                    adapter=v2,
                )
            ]
        ),
    )
    result = operation.as_strategy().example()
    assert isinstance(result.body, str)
    b64decode(result.body)


@pytest.mark.parametrize(
    ("values", "error"),
    [
        (("valid", "invalid"), f"strategy must be of type {st.SearchStrategy}, not {str}"),
        ((123, st.from_regex(r"\d")), f"name must be of type {str}, not {int}"),
    ],
)
def test_invalid_custom_strategy(values, error):
    with pytest.raises(TypeError) as exc:
        schemathesis.openapi.format(*values)
    assert error in str(exc.value)


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "definition", [{"name": "api_key", "in": "header", "type": "string"}, {"name": "api_key", "in": "header"}]
)
def test_valid_headers(ctx, swagger_20, definition):
    api = ctx.openapi.apps.success()
    operation = APIOperation(
        "/api/success",
        "GET",
        definition=OperationDefinition({}),
        schema=swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
        base_url=api.base_url,
        headers=OpenApiParameterSet(
            ParameterLocation.HEADER,
            [OpenApiParameter.from_definition(definition=definition, name_to_uri={}, adapter=v2)],
            adapter=v2,
        ),
    )

    @given(case=operation.as_strategy())
    @settings(suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow], deadline=None, max_examples=10)
    def inner(case):
        case.call()

    inner()


def make_swagger(*parameters):
    return {
        "swagger": "2.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/form": {
                "post": {
                    "parameters": list(parameters),
                    "summary": "Returns a list of users.",
                    "description": "Optional extended description in Markdown.",
                    "consumes": ["multipart/form-data"],
                    "produces": ["application/json"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.mark.parametrize(
    "raw_schema",
    [
        make_swagger(
            {"name": "a", "in": "formData", "required": True, "type": "number"},
            {"name": "b", "in": "formData", "required": True, "type": "boolean"},
            {"name": "c", "in": "formData", "required": True, "type": "array"},
        ),
        make_swagger({"name": "c", "in": "formData", "required": True, "type": "array"}),
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
            "paths": {
                "/form": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "a": {"type": "number"},
                                            "b": {"type": "boolean"},
                                            "c": {"type": "array"},
                                        },
                                        "required": ["a", "b", "c"],
                                    },
                                }
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    ],
)
@pytest.mark.hypothesis_nested
def test_valid_form_data(ctx, raw_schema):
    api = ctx.openapi.apps.success()
    # When the request definition contains a schema, matching values of which cannot be encoded to multipart
    # straightforwardly
    schema = ctx.openapi.from_full_schema(raw_schema)
    schema.config.update(base_url=f"{api.base_url}/api")

    @given(case=schema["/form"]["POST"].as_strategy())
    @settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=10)
    def inner(case):
        case.call()

    # Then these values should be cast to bytes and handled successfully
    inner()


@pytest.mark.hypothesis_nested
def test_optional_form_data(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/form": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "string",
                                },
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # When the multipart form is optional
    # Note, this test is similar to the one above, but has a simplified schema & conditions
    # It is done mostly due to performance reasons
    schema.config.update(base_url=f"{api.base_url}/api")

    @given(case=schema["/form"]["POST"].as_strategy())
    @settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], max_examples=1)
    def inner(case):
        assume(case.body is NOT_SET)
        case.call()

    # Then payload can be absent
    inner()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (".", "%2E"),
        ("..", "%2E%2E"),
        (".foo", ".foo"),
        ("%2E", "%2E"),
        ("%2e", "%2E"),
        ("%2E%2E", "%2E%2E"),
        ("%2e%2e", "%2E%2E"),
        # Spaces in path segments must percent-encode, not form-encode (GH-4252)
        ("2 m above gnd", "2%20m%20above%20gnd"),
        ("a+b", "a%2Bb"),
    ],
)
def test_path_parameters_quotation(value, expected):
    # See GH-1036
    assert quote_all({"foo": value})["foo"] == expected


def test_path_parameter_space_encoded_in_url(ctx):
    # GH-4252: a space in a path segment must reach the wire as "%20", never "+"
    schema = ctx.openapi.load_schema(
        {"/forecast/{level}": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version="3.1.0",
    )
    operation = schema["/forecast/{level}"]["GET"]
    case = operation.Case(path_parameters=quote_all({"level": "2 m above gnd"}))
    assert schema.build_request_url(case, "http://127.0.0.1") == "http://127.0.0.1/forecast/2%20m%20above%20gnd"


@pytest.mark.parametrize("expected", ["null", "true", "false"])
def test_parameters_jsonified(ctx, expected):
    # See GH-1166
    # When `None` or `True` / `False` are generated in path or query
    schema = ctx.openapi.load_schema(
        {
            "/foo/{param_path}": {
                "get": {
                    "parameters": [
                        {
                            "name": f"param_{location}",
                            "in": location,
                            "required": True,
                            "schema": {"type": "boolean", "nullable": True},
                        }
                        for location in ("path", "query")
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    strategy = schema["/foo/{param_path}"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(deadline=None, max_examples=1)
    def test(case):
        # Then they should be converted to their JSON equivalents
        assume(case.path_parameters["param_path"] == expected)
        assume(case.query["param_query"] == expected)

    test()


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
def test_optional_payload(ctx, version):
    # When body are not required
    paths = {"/users": {"post": {"responses": {"200": {"description": "OK"}}}}}
    if version == "2.0":
        paths["/users"]["post"]["parameters"] = [{"in": "body", "name": "body", "schema": {"type": "string"}}]
    else:
        paths["/users"]["post"]["requestBody"] = {"content": {"application/json": {"schema": {"type": "string"}}}}
    schema = ctx.openapi.load_schema(paths, version=version)
    strategy = schema["/users"]["post"].as_strategy()
    # Then `None` could be generated by Schemathesis
    assert find(strategy, lambda x: x.body is NOT_SET).body is NOT_SET


@given(data=st.data())
@settings(deadline=None)
def test_date_format(data):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/data": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "format": "date",
                                    "type": "string",
                                },
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    strategy = schema["/data"]["POST"].as_strategy()
    case = data.draw(strategy)
    datetime.datetime.strptime(case.body, "%Y-%m-%d")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"foo": True}, {"foo": "true"}),
        ({"foo": False}, {"foo": "false"}),
        ({"foo": None}, {"foo": "null"}),
        ([{"foo": None}], [{"foo": "null"}]),
        ([{"foo": {"bar": True}}], [{"foo": {"bar": "true"}}]),
    ],
)
def test_jsonify_python_specific_types(value, expected):
    assert jsonify_python_specific_types(value) == expected


def test_health_check_failed_large_base_example(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"type": "integer"}, "minItems": 100000}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    # Then it should be able to generate requests
    assert (
        cli.run(
            str(schema_path), "--max-examples=1", f"--url={api.base_url}/api", "--phases=fuzzing", "--mode=positive"
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("discriminator", "valid_values"),
    [
        ({"propertyName": "petType"}, {"Cat", "Dog"}),
        (
            {
                "propertyName": "petType",
                "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
            },
            {"feline", "canine", "Cat", "Dog"},
        ),
    ],
    ids=["implicit", "explicit"],
)
def test_discriminator_property_pinned_in_generation(ctx, discriminator, valid_values):
    schema = ctx.openapi.load_schema(
        {
            "/pets": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {"$ref": "#/components/schemas/Cat"},
                                        {"$ref": "#/components/schemas/Dog"},
                                    ],
                                    "discriminator": discriminator,
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Cat": {"type": "object", "properties": {"petType": {"type": "string"}}, "required": ["petType"]},
                "Dog": {"type": "object", "properties": {"petType": {"type": "string"}}, "required": ["petType"]},
            }
        },
    )

    @given(case=schema["/pets"]["POST"].as_strategy())
    @settings(max_examples=10, database=None, phases=[Phase.generate])
    def inner(case):
        assert case.body["petType"] in valid_values

    inner()


def test_hypothesis_observability_serialization(ctx):
    # Hypothesis observability serializes all dataclass fields on generated values
    schema = ctx.openapi.load_schema({"/test": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @given(case=schema["/test"]["GET"].as_strategy())
    @settings(max_examples=1, database=None, phases=[Phase.generate])
    def test(case):
        pass

    with with_observability_callback(lambda _: None):
        test()


@pytest.mark.parametrize("location", [ParameterLocation.QUERY, ParameterLocation.HEADER])
def test_apply_exclusions_drops_empty_required_when_all_filtered(location):
    # Empty `required` violates the OpenAPI meta-schema and crashes Hypothesis draws.
    pset = OpenApiParameterSet(location, items=[], adapter=v2)
    pset._schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
        "additionalProperties": False,
    }
    out = pset.get_schema_with_exclusions(exclude=["key"])
    assert "required" not in out


def test_apply_exclusions_reachable_via_auth_supplied_required_header():
    pset = OpenApiParameterSet(
        ParameterLocation.HEADER,
        [
            OpenApiParameter.from_definition(
                definition={
                    "in": "header",
                    "name": "Authorization",
                    "required": True,
                    "type": "string",
                },
                name_to_uri={},
                adapter=v2,
            )
        ],
        adapter=v2,
    )
    out = pset.get_schema_with_exclusions(exclude=["Authorization"])
    assert "required" not in out
    assert out["properties"] == {}


@pytest.mark.parametrize(
    ("format", "minimum", "maximum"),
    [("int32", -(2**31), 2**31 - 1), ("int64", -(2**63), 2**63 - 1)],
)
def test_positive_integers_stay_within_format_range(ctx, format, minimum, maximum):
    # `format` alone declares the range; without it generation draws arbitrary-precision ints
    # that a fixed-width-int server rejects, turning a positive case into a false failure.
    schema = ctx.openapi.load_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"value": {"type": "integer", "format": format}},
                                    "required": ["value"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )

    @given(schema["/x"]["POST"].as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=250, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert minimum <= case.body["value"] <= maximum, f"Out of {format} range: {case.body['value']}"

    test()


def test_integer_multiple_of_body(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "integer", "multipleOf": 3, "minimum": 1}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None)
    def test(case):
        assert case.body >= 1 and case.body % 3 == 0, case.body

    test()


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
@pytest.mark.parametrize(
    ("body_schema", "extract"),
    [
        ({"type": "string", "format": "uuid"}, lambda body: body),
        (
            {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}, "required": ["id"]},
            lambda body: body["id"],
        ),
    ],
    ids=["bare", "property"],
)
def test_format_constrained_string_body(ctx, version, body_schema, extract):
    # Draft 2020-12 reads `format` as an annotation, which must not turn `uuid` into arbitrary text.
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None)
    def test(case):
        uuid.UUID(extract(case.body))

    test()


CANONICAL_NUMBER_SCHEMAS = [
    {"type": "number", "minimum": 1, "maximum": 5},
    {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
    {"type": "number", "minimum": 1.5, "exclusiveMaximum": 2.5},
    {"type": "number", "maximum": 0},
    {"type": "number", "multipleOf": 0.5},
    {"type": "number", "multipleOf": 0.1, "minimum": -1, "maximum": 1},
    {"type": "number", "multipleOf": 1e-20, "minimum": 1.1, "maximum": 1.1000000000000003},
    {"type": "number", "multipleOf": 1.5, "exclusiveMinimum": 0},
    {"type": "number", "multipleOf": 0.5, "exclusiveMinimum": 1, "exclusiveMaximum": 2.5},
    {"type": "number", "allOf": [{"multipleOf": 0.25}, {"multipleOf": 0.75}]},
    {"type": "number", "minimum": -1e308, "maximum": 1e308},
    {"type": "number", "exclusiveMinimum": 0, "maximum": 1e-320},
    # Bounds outside the float range: only the representable part of the interval can be drawn.
    {"type": "number", "minimum": -(10**400), "maximum": 10**400},
    # No float clears the minimum, leaving the integers above it.
    {"type": "number", "minimum": 10**400},
    # A fractional grid reaches past the float range.
    {"type": "number", "multipleOf": 0.3, "minimum": 10**308},
    # The nearest float to the bound sits above it, yet counts as the bound once compared as a float.
    {"type": "number", "exclusiveMinimum": 10**25, "maximum": 1e308},
    {"type": "number", "minimum": -1e308, "exclusiveMaximum": -(10**25)},
    # No float lies at or below the maximum.
    {"type": "number", "maximum": -(10**400)},
    # Grid points round to a float that one of the two readings puts outside the bounds.
    {"type": "number", "multipleOf": 0.1, "minimum": 10**18 - 1000, "maximum": 10**18 - 1},
]

CANONICAL_INTEGER_SCHEMAS = [
    {"type": "integer", "minimum": 1, "maximum": 10},
    {"type": "integer", "multipleOf": 7, "minimum": 0, "maximum": 10**6},
    # Two `multipleOf` values the canonicalizer cannot fold into one.
    {"type": "number", "allOf": [{"multipleOf": 1e308}, {"multipleOf": 3e307}]},
]

CANONICAL_OBJECT_SCHEMAS = [
    {"type": "object"},
    {"type": "object", "properties": {"a": {"type": "integer"}}},
    {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
    {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}, "required": ["a"]},
    {"type": "object", "properties": {"a": {"type": "string", "minLength": 2}}},
    {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "integer"}}}}},
    # A required key the schema says nothing else about.
    {"type": "object", "required": ["a"]},
    {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a", "b"]},
    {"type": "object", "properties": {"a": {"anyOf": [{"type": "integer"}, {"type": "string"}]}}},
    {
        "allOf": [
            {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
            {"type": "object", "properties": {"a": {"minimum": 5}, "b": {"type": "string"}}},
        ]
    },
    {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": {"type": "string"}},
    {"type": "object", "additionalProperties": {"type": "integer"}},
    # The required key is absent from `properties`, so its value answers to `additionalProperties`.
    {"type": "object", "required": ["a"], "additionalProperties": {"type": "string", "minLength": 2}},
    {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["a"],
        "additionalProperties": {"type": "array", "items": {"type": "integer"}},
    },
    {"type": "object", "additionalProperties": {"type": "object", "properties": {"b": {"type": "integer"}}}},
    {"type": "object", "additionalProperties": {"anyOf": [{"type": "integer"}, {"type": "boolean"}]}},
    {"type": "object", "minProperties": 2},
    {"type": "object", "maxProperties": 2},
    {"type": "object", "minProperties": 2, "maxProperties": 2},
    # A floor beyond the extra keys drawn by default.
    {"type": "object", "minProperties": 8},
    {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}, "minProperties": 1},
    {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "maxProperties": 1},
    {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "additionalProperties": False,
        "minProperties": 2,
    },
    {"type": "object", "propertyNames": {"enum": ["a", "b", "c"]}, "minProperties": 2},
    {"type": "object", "propertyNames": {"maxLength": 3}},
    {"type": "object", "propertyNames": {"pattern": "^x"}, "maxProperties": 3},
    {"type": "object", "propertyNames": {"pattern": "x[0-9]+"}, "minProperties": 2},
    {"type": "object", "propertyNames": {"maxLength": 4, "pattern": "^x"}, "minProperties": 2},
    {"type": "object", "propertyNames": {"anyOf": [{"pattern": "x[0-9]+"}, {"maxLength": 3}]}},
    # A required key `properties` says nothing about, but a pattern does.
    {"type": "object", "patternProperties": {"^a": {"type": "integer"}}, "required": ["ax"]},
    {"type": "object", "properties": {"ab": {"minimum": 1}}, "patternProperties": {"^a": {"type": "integer"}}},
    {
        "type": "object",
        "patternProperties": {"^a": {"type": "integer"}},
        "additionalProperties": False,
        "minProperties": 2,
    },
    {"type": "object", "patternProperties": {"^a": {"type": "integer"}}, "propertyNames": {"enum": ["a1", "zz"]}},
    # Python reads `\d` as any Unicode digit, the validator as ASCII only.
    {"type": "object", "patternProperties": {"^\\d": {"type": "integer"}}},
]

CANONICAL_ARRAY_SCHEMAS = [
    {"type": "array", "items": {"type": "integer"}},
    {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
    {"type": "array", "items": {"type": "integer"}, "uniqueItems": True, "minItems": 3},
    {"type": "array", "items": {"type": "number"}, "uniqueItems": True, "minItems": 2},
    {"type": "array", "items": True, "uniqueItems": True, "minItems": 2, "maxItems": 5},
    {"type": "array", "items": {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}},
    {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}, "maxItems": 3},
    {"type": "object", "properties": {"xs": {"type": "array", "items": {"type": "integer"}}}, "required": ["xs"]},
    {"type": "array", "items": {"anyOf": [{"type": "integer"}, {"type": "string"}]}},
    {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 3},
    {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}]},
    {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}], "items": False},
    {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}], "maxItems": 1},
    {"type": "array", "prefixItems": [{"type": "integer"}], "items": {"type": "string"}, "minItems": 3},
    {"type": "array", "prefixItems": [{"type": "integer"}], "items": {"type": "string"}, "maxItems": 4},
    {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "integer"}], "uniqueItems": True, "minItems": 4},
    {"type": "array", "prefixItems": [{"type": "object", "required": ["a"]}], "items": {"type": "array"}},
    {"type": "array", "contains": {"type": "integer"}},
    {"type": "array", "items": {"type": "number"}, "contains": {"type": "integer"}},
    {"type": "array", "items": {"type": "string"}, "contains": {"const": "A"}},
    {"type": "array", "contains": {"type": "integer"}, "minItems": 3},
    {"type": "array", "contains": {"type": "integer"}, "maxItems": 1},
    {"type": "array", "contains": {"const": "A"}, "minContains": 2},
    {"type": "array", "items": {"type": "integer"}, "contains": {"minimum": 10}, "minContains": 3, "minItems": 5},
    {"type": "array", "prefixItems": [{"type": "string"}], "contains": {"type": "integer"}},
    {"type": "array", "contains": {"type": "integer"}, "maxContains": 2},
    {
        "type": "array",
        "items": {"type": "integer"},
        "contains": {"minimum": 10},
        "minContains": 2,
        "maxContains": 3,
        "minItems": 5,
    },
    {"type": "array", "items": {"type": "integer"}, "contains": {"type": "integer"}, "maxContains": 3, "minItems": 3},
    {
        "type": "array",
        "items": {"type": "string"},
        "contains": {"type": "string", "minLength": 5},
        "uniqueItems": True,
        "minItems": 3,
    },
    # Matches and filler come from disjoint schemas, so uniqueness holds across the join.
    {"type": "array", "contains": {"type": "integer"}, "maxContains": 2, "uniqueItems": True},
    # Nothing appended can meet the demand, so a pinned position takes it on.
    {
        "type": "array",
        "prefixItems": [{"type": "integer"}],
        "items": {"type": "string"},
        "contains": {"type": "integer", "minimum": 10},
    },
    # A pinned position that may match by chance is steered away from a bounded demand.
    {"type": "array", "prefixItems": [{"type": "integer"}], "contains": {"minimum": 10}, "maxContains": 1},
    # Where it cannot be steered, the count is confirmed against the schema instead.
    {"type": "array", "prefixItems": [{"type": "string"}], "contains": {"type": "integer"}, "maxContains": 1},
    # Every element matches, so the demand takes on the slack the ceiling and the length bound leave.
    {"type": "array", "items": {"type": "integer"}, "contains": {"type": "integer"}, "maxContains": 3, "maxItems": 5},
    # A ceiling keeps two demands apart that would otherwise fold into one element.
    {
        "type": "array",
        "uniqueItems": True,
        "allOf": [{"contains": {"const": "A"}, "maxContains": 2}, {"contains": {"const": "B"}}],
    },
    # Distinct values run out before the ceiling does, so the demand takes no slack.
    {
        "type": "array",
        "items": {"enum": [1, 2]},
        "contains": {"enum": [1, 2]},
        "maxContains": 2,
        "uniqueItems": True,
        "minItems": 2,
    },
    # The intersection stays an unfolded `allOf`, so the demand becomes a filter.
    {
        "type": "array",
        "items": {"type": "string"},
        "contains": {"$ref": "#/$defs/long"},
        "$defs": {"long": {"minLength": 4}},
    },
    # Same for the filler the ceiling needs.
    {
        "type": "array",
        "items": {"type": "string"},
        "contains": {"$ref": "#/$defs/long"},
        "maxContains": 1,
        "$defs": {"long": {"minLength": 4}},
    },
    # A demand met by the truncated prefix must not append past where it stops.
    {
        "type": "array",
        "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [1, 2]}],
        "contains": {"type": "integer"},
        "uniqueItems": True,
    },
    # The same where a ceiling would otherwise hand it slack to append into.
    {
        "type": "array",
        "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [1, 2]}],
        "contains": {"minimum": 10},
        "minContains": 0,
        "maxContains": 1,
        "uniqueItems": True,
    },
]

# Formats the validator asserts, so generated values can be checked against the schema itself.
ASSERTED_FORMATS = [
    "date",
    "date-time",
    "time",
    "duration",
    "email",
    "idn-email",
    "hostname",
    "idn-hostname",
    "ipv4",
    "ipv6",
    "uri",
    "uri-reference",
    "uri-template",
    "iri",
    "iri-reference",
    "json-pointer",
    "relative-json-pointer",
    "uuid",
]

# Each row pairs a schema with predicates for values that must be drawable from it. An empty tuple
# checks only that every drawn value is valid.
CANONICAL_CASES = [
    *[(schema, ()) for schema in CANONICAL_NUMBER_SCHEMAS],
    *[(schema, ()) for schema in CANONICAL_INTEGER_SCHEMAS],
    *[(schema, ()) for schema in CANONICAL_OBJECT_SCHEMAS],
    *[(schema, ()) for schema in CANONICAL_ARRAY_SCHEMAS],
    *[({"type": "string", "format": name}, ()) for name in ASSERTED_FORMATS],
    # An unknown format is an annotation: it must neither block generation nor narrow the strings.
    ({"type": "string", "format": "decimal", "minLength": 3, "maxLength": 6}, ()),
    ({"type": "string", "format": "uuid", "minLength": 36, "maxLength": 36}, ()),
    ({"type": "string", "format": "hostname", "pattern": "^a"}, ()),
    # A pattern is a search, so the value may carry anything around the match.
    ({"type": "string", "pattern": "^x"}, (lambda value: len(value) > 1,)),
    # Python's `re` matches `$` before a trailing newline; the validator does not.
    ({"type": "string", "pattern": "x$"}, ()),
    ({"type": "string", "pattern": "[0-9]{2}", "maxLength": 4}, ()),
    # Python reads the shorthand classes over the whole of Unicode, the validator over ASCII only.
    ({"type": "string", "pattern": "^\\d+"}, (lambda value: len(value) > 1,)),
    ({"type": "string", "pattern": "^[\\w]+"}, ()),
    ({"type": "string", "pattern": "\\s"}, ()),
    # A `$ref` names the schema to draw from, and repeats of it draw the same way.
    ({"$ref": "#/$defs/text", "$defs": {"text": {"type": "string", "minLength": 3}}}, ()),
    (
        {
            "type": "object",
            "properties": {"a": {"$ref": "#/$defs/text"}, "b": {"$ref": "#/$defs/text"}},
            "required": ["a", "b"],
            "$defs": {"text": {"type": "string", "minLength": 3}},
        },
        (),
    ),
    ({"type": "array", "items": {"$ref": "#/$defs/n"}, "$defs": {"n": {"type": "integer"}}}, ()),
    # A pointer through another pointer.
    ({"$ref": "#/$defs/a", "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"type": "integer"}}}, ()),
    # A schema that names itself: shallow instances are instances, and deeper ones stay reachable.
    (
        {
            "$ref": "#/$defs/node",
            "$defs": {
                "node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/$defs/node"}, "value": {"type": "integer"}},
                    "required": ["value"],
                }
            },
        },
        (lambda value: isinstance(value.get("child"), dict),),
    ),
    # Two schemas that name each other.
    (
        {
            "$ref": "#/$defs/a",
            "$defs": {
                "a": {"type": "object", "properties": {"b": {"$ref": "#/$defs/b"}}},
                "b": {"type": "object", "properties": {"a": {"$ref": "#/$defs/a"}}},
            },
        },
        (),
    ),
    # Anything at all, and a property nothing satisfies.
    ({}, ()),
    ({"type": "object", "properties": {"a": False}}, (lambda value: "a" not in value,)),
    # A key the pattern claims is drawable even though nothing names it.
    (
        {"type": "object", "patternProperties": {"^a": {"type": "integer"}}},
        (lambda value: any(key.startswith("a") for key in value),),
    ),
    # Each pattern names keys of its own; one claimed by both is only reachable by chance.
    (
        {"type": "object", "patternProperties": {"^a": {"type": "integer"}, "b$": {"multipleOf": 2}}},
        (
            lambda value: any(key.startswith("a") for key in value),
            lambda value: any(key.endswith("b") for key in value),
        ),
    ),
    # A pattern Python `re` rejects names no keys, but the rest of the schema still generates.
    ({"type": "object", "patternProperties": {r"\p{L}": {"type": "integer"}}}, ()),
    # A key both patterns claim answers to both schemas.
    (
        {
            "type": "object",
            "patternProperties": {"^a": {"type": "integer"}, "^ab": {"multipleOf": 2}},
            "additionalProperties": False,
            "minProperties": 1,
        },
        (lambda value: any(key.startswith("ab") for key in value),),
    ),
    # A closed schema whose only admitted keys come from the pattern.
    (
        {"type": "object", "patternProperties": {"^a": {"type": "integer"}}, "additionalProperties": False},
        (lambda value: len(value) > 1,),
    ),
    # A ceiling narrows the sizes, it does not pin the object to its floor.
    (
        {"type": "object", "properties": {"a": {"type": "integer"}}, "maxProperties": 3},
        (lambda value: len(value) == 3,),
    ),
    # A floor past the extras drawn by default does not pin the object either.
    ({"type": "object", "minProperties": 6}, (lambda value: len(value) > 6,)),
    ({"type": "object", "minProperties": 6, "maxProperties": 8}, (lambda value: len(value) == 8,)),
    (
        {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
        (lambda value: set(value) - {"a"},),
    ),
    (
        {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": {"type": "string"}},
        (lambda value: set(value) - {"a"},),
    ),
    (
        {"type": "array", "prefixItems": [{"type": "integer"}], "items": {"type": "string"}},
        (lambda value: len(value) > 1,),
    ),
    # Arrays shorter than the prefix are instances whenever the floor allows them.
    (
        {"type": "array", "prefixItems": [{"const": 1}, {"const": 2}]},
        (lambda value: len(value) == 0, lambda value: len(value) == 1),
    ),
    (
        {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}], "minItems": 1, "maxItems": 4},
        (lambda value: len(value) == 1,),
    ),
    # A cycle in a prefix position admits no value there; the shorter arrays are the instances.
    (
        {"type": "array", "prefixItems": [{"$ref": "#"}, {"type": "integer"}], "maxItems": 3},
        (lambda value: len(value) == 0,),
    ),
    # `#` names the document itself; canonicalization resolves these cycles before generation sees them.
    ({"type": "object", "properties": {"child": {"$ref": "#"}}}, ()),
    ({"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#"}}}}, ()),
    # An array reaching past the demanded element, not only the shortest one that meets the demand.
    ({"type": "array", "items": {"type": "integer"}, "contains": {"const": 1}}, (lambda value: len(value) > 1,)),
    # Two `contains` demands the canonical form keeps side by side; both values land.
    (
        {
            "type": "array",
            "items": {"type": "string"},
            "allOf": [{"contains": {"const": "A"}}, {"contains": {"const": "B"}}],
        },
        (lambda value: "A" in value and "B" in value,),
    ),
    # Demands pinned to distinct values can share a unique array.
    (
        {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
            "allOf": [{"contains": {"const": "A"}}, {"contains": {"const": "B"}}],
        },
        (lambda value: "A" in value and "B" in value,),
    ),
    # Every item matches the demand, so the ceiling — not the filler — carries the lower size bound.
    (
        {
            "type": "array",
            "items": {"type": "string", "pattern": "^a{5,}$"},
            "contains": {"minLength": 3},
            "maxContains": 5,
            "minItems": 3,
        },
        (lambda value: len(value) > 3,),
    ),
    # Only the prefix can meet the demand, so that position takes it on — and the tail still grows.
    (
        {
            "type": "array",
            "prefixItems": [{"type": "integer"}],
            "items": {"type": "string"},
            "contains": {"type": "integer"},
        },
        (lambda value: len(value) > 1,),
    ),
    # Both demands are met by the same value, so one element covers them in a unique array.
    (
        {
            "type": "array",
            "items": {"type": "string"},
            "allOf": [{"contains": {"const": "A"}}, {"contains": {"pattern": "^A$"}}],
            "uniqueItems": True,
        },
        (lambda value: value == ["A"], lambda value: len(value) > 1),
    ),
    # The prefix position cannot help but match, so it is the one match the ceiling admits.
    (
        {"type": "array", "prefixItems": [{"type": "integer"}], "contains": {"type": "integer"}, "maxContains": 1},
        (lambda value: len(value) > 1,),
    ),
    # Several distinct matches, drawn apart from the filler they cannot equal.
    (
        {
            "type": "array",
            "items": {"type": "integer"},
            "contains": {"minimum": 10},
            "minContains": 3,
            "maxContains": 4,
            "uniqueItems": True,
            "minItems": 5,
        },
        (lambda value: sum(item >= 10 for item in value) == 3 and any(item < 10 for item in value),),
    ),
    # A ceiling of zero: every position has to avoid the demand.
    (
        {"type": "array", "contains": {"type": "integer"}, "minContains": 0, "maxContains": 0, "minItems": 2},
        (lambda value: len(value) > 2,),
    ),
    # Three positions over a two-value domain cannot all differ, so the array stops early — but it
    # still reaches the length that does work, in either order.
    (
        {"type": "array", "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [1, 2]}], "uniqueItems": True},
        (lambda value: value == [1, 2], lambda value: value == [2, 1]),
    ),
    # A demand contributing no element leaves nothing to build from its schema, and the array is
    # still more than the empty one that trivially clears it.
    (
        {
            "type": "array",
            "contains": {"type": "object", "patternProperties": {"^a": {"type": "integer"}}},
            "minContains": 0,
            "maxContains": 2,
        },
        (lambda value: len(value) > 0,),
    ),
    # The length ceiling leaves `items` governing no position.
    (
        {
            "type": "array",
            "prefixItems": [{"type": "integer"}],
            "items": {"$ref": "#/$defs/unbuildable"},
            "maxItems": 1,
            "$defs": {"unbuildable": {"type": "object", "minProperties": 2}},
        },
        (lambda value: len(value) == 1,),
    ),
    # A position may take the demand on, so the shortest array is reachable — and the longer one.
    (
        {"type": "array", "prefixItems": [{"type": "integer"}], "contains": {"const": 7}, "maxItems": 2},
        (lambda value: value == [7], lambda value: len(value) == 2),
    ),
    # The same where the ceiling admits only that one match.
    (
        {"type": "array", "prefixItems": [{"type": "string"}], "contains": {"minLength": 4}, "maxContains": 1},
        (lambda value: len(value) == 1, lambda value: len(value) > 1),
    ),
    # A demand the prefix already meets asks for no distinct value of its own.
    (
        {
            "type": "array",
            "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [1, 2]}],
            "contains": {"enum": [1, 2]},
            "uniqueItems": True,
        },
        (lambda value: len(value) == 2,),
    ),
    # A demand the prefix meets contributes nothing appendable; the next demand absorbs the floor.
    (
        {
            "type": "array",
            "prefixItems": [{"type": "integer"}],
            "items": {"type": "string"},
            "allOf": [{"contains": {"type": "integer"}}, {"contains": {"type": "string"}, "maxContains": 3}],
            "minItems": 4,
        },
        (lambda value: len(value) == 4,),
    ),
    # A pinned position closes the tail, and the shorter arrays are still instances.
    (
        {"type": "array", "prefixItems": [{"type": "integer"}], "items": False},
        (lambda value: len(value) == 0, lambda value: len(value) == 1),
    ),
    # Both pinned positions cannot help but match the bounded demand, so only shorter arrays remain.
    (
        {
            "type": "array",
            "prefixItems": [{"type": "integer"}, {"type": "integer"}],
            "contains": {"type": "integer"},
            "maxContains": 1,
        },
        (lambda value: len(value) == 1,),
    ),
    # The tail runs out of distinct values below the floor, so the demanded element fills the rest.
    (
        {
            "type": "array",
            "items": {"enum": [1, 2, 3]},
            "contains": {"const": 2},
            "minContains": 0,
            "maxContains": 1,
            "minItems": 3,
            "uniqueItems": True,
        },
        (lambda value: len(value) == 3,),
    ),
    # The wide demand comes first and is absorbed by the stricter bounded one behind it.
    (
        {
            "type": "array",
            "items": {"enum": ["A", "X"]},
            "uniqueItems": True,
            "allOf": [{"contains": {"enum": ["A", "B"]}}, {"contains": {"const": "A"}, "maxContains": 2}],
        },
        (lambda value: value == ["A"],),
    ),
    (
        {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
            "allOf": [{"contains": {"minLength": 1}}, {"contains": {"minLength": 2}, "maxContains": 2}],
        },
        (lambda value: len(value) == 1 and len(value[0]) >= 2,),
    ),
    # A prefix too wide to match cheaply skips the distinct-value check and still draws in full.
    (
        {
            "type": "array",
            "uniqueItems": True,
            "minItems": 17,
            "prefixItems": [{"enum": [index, index + 100]} for index in range(17)],
        },
        (lambda value: len(value) == 17,),
    ),
    # A demand inside another demand's domain cannot be steered off it; the net has to count.
    (
        {
            "type": "array",
            "items": {"enum": ["A", "B"]},
            "allOf": [{"contains": {"enum": ["A", "B"]}, "maxContains": 3}, {"contains": {"const": "A"}}],
        },
        (lambda value: len(value) > 1,),
    ),
    # One element meets both demands at once, so they do not need a distinct value each.
    (
        {
            "type": "array",
            "uniqueItems": True,
            "items": {"enum": ["A", "X"]},
            "allOf": [{"contains": {"const": "A"}, "maxContains": 2}, {"contains": {"enum": ["A", "B"]}}],
        },
        (lambda value: value == ["A"],),
    ),
    # Only one assignment hands every position a distinct value, and it is not the greedy one.
    (
        {
            "type": "array",
            "uniqueItems": True,
            "minItems": 4,
            "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [3, 4]}, {"enum": [1, 3]}],
        },
        (lambda value: len(value) == 4,),
    ),
    # Extra keys have no value to carry — the object stays, with nothing but its named keys.
    (
        {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {"type": "integer"},
                "contains": {"type": "integer"},
                "maxContains": 2,
                "minItems": 4,
            },
        },
        (lambda value: value == {},),
    ),
    # The demand, the elements, and the ceiling-holding filler all meet through `$defs` pointers.
    (
        {
            "type": "array",
            "items": {"$ref": "#/$defs/text"},
            "contains": {"$ref": "#/$defs/long"},
            "maxContains": 1,
            "minItems": 2,
            "$defs": {"text": {"type": "string"}, "long": {"minLength": 4}},
        },
        (lambda value: any(len(item) >= 4 for item in value) and any(len(item) < 4 for item in value),),
    ),
    (
        {
            "type": "array",
            "items": {"$ref": "#/$defs/text"},
            "contains": {"$ref": "#/definitions/long"},
            "$defs": {"text": {"type": "string"}},
            "definitions": {"long": {"minLength": 4}},
        },
        (lambda value: any(len(item) >= 4 for item in value) and any(len(item) < 4 for item in value),),
    ),
    # A pattern demand cannot be negated constructively, so the filler holds the ceiling by filter.
    (
        {"type": "array", "items": {"type": "string"}, "contains": {"pattern": "^a"}, "maxContains": 1, "minItems": 2},
        (lambda value: len(value) >= 2,),
    ),
    # Within `items` the demands' matches are disjoint, so folding them would lose both.
    (
        {
            "type": "array",
            "items": {"enum": [1, "aa"]},
            "uniqueItems": True,
            "allOf": [{"contains": {"enum": [1, "bb"]}}, {"contains": {"enum": ["aa", "bb"]}}],
        },
        (lambda value: len(value) == 2,),
    ),
    # One demand's element may not land on another demand's ceiling.
    (
        {
            "type": "array",
            "items": {"type": "string"},
            "allOf": [{"contains": {"const": "A"}, "maxContains": 1}, {"contains": {"minLength": 1}}],
        },
        (lambda value: len(value) > 1,),
    ),
    (
        {
            "type": "array",
            "items": {"enum": ["A", "B"]},
            "allOf": [
                {"contains": {"const": "A"}, "maxContains": 1},
                {"contains": {"enum": ["A", "B"]}, "minContains": 2},
            ],
        },
        (lambda value: len(value) > 2,),
    ),
]
# NOT `ids=str`: pytest applies an `ids` callable per parameter, so a predicate tuple stringifies with
# a memory address and `pytest -n auto` aborts with "Different tests were collected between gw0 and
# gw11". Derive the ids from the schemas alone.
CANONICAL_CASE_IDS = [str(schema) for schema, _ in CANONICAL_CASES]


@pytest.mark.parametrize(("schema", "reaches"), CANONICAL_CASES, ids=CANONICAL_CASE_IDS)
def test_canonical_generation(schema, reaches):
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema, validate_formats=True).is_valid

    # A format generator cannot be steered by a pattern or a length, so those draws are discarded.
    @given(built)
    @settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    def test(value):
        assert is_valid(value), value

    test()


@pytest.mark.xfail(reason="pure reference cycles fold to `true` in an upcoming jsonschema-rs release", strict=True)
@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#"},
        {"allOf": [{"$ref": "#"}]},
        {"$ref": "#/$defs/a", "$defs": {"a": {"$ref": "#"}}},
    ],
    ids=["direct", "all-of", "via-definition"],
)
def test_degenerate_root_cycle(schema):
    # A pure `#` cycle admits everything the validator does.
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert is_valid(value), value

    test()


@pytest.mark.xfail(reason="needs a validator built from the canonical node itself in jsonschema-rs", strict=True)
def test_pattern_properties_root_reference_overlap():
    # A name claimed by both patterns must satisfy both schemas, `#` meaning the whole document.
    schema = {"type": "object", "patternProperties": {"^a": {"type": "integer"}, "a": {"$ref": "#"}}}
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    def test(value):
        assert is_valid(value), value

    test()


def test_canonical_contains_conflicting_formats_never_unsound():
    # No string is both an ipv4 and a date; the filter must reject every draw, not pass one format-blind.
    schema = {
        "type": "array",
        "items": {"type": "string"},
        "contains": {"allOf": [{"format": "ipv4"}, {"format": "date"}]},
    }
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema, validate_formats=True).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
    def test(value):
        assert is_valid(value), value

    with pytest.raises(Unsatisfiable):
        test()


def test_pattern_properties_root_reference_single_pattern():
    # Without overlap no cross-pattern filter exists, so `#` values stay modeled.
    schema = {"type": "object", "patternProperties": {"^a": {"$ref": "#"}}}
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    def test(value):
        assert is_valid(value), value

    test()


@pytest.mark.parametrize(
    ("schema", "reaches"),
    [case for case in CANONICAL_CASES if case[1]],
    ids=[case_id for case_id, case in zip(CANONICAL_CASE_IDS, CANONICAL_CASES, strict=True) if case[1]],
)
def test_canonical_generation_reaches(schema, reaches):
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    # High budget: each predicate looks for one specific value, not a property of every draw.
    for predicate in reaches:
        find(built, predicate, settings=settings(max_examples=1000, database=None))


@pytest.mark.parametrize("schema", CANONICAL_INTEGER_SCHEMAS, ids=str)
def test_canonical_integer_generation_emits_python_ints(schema):
    # `1.0` satisfies `type: integer` in JSON terms, so the validator alone would accept a float here.
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    @given(built)
    @settings(max_examples=10, deadline=None)
    def test(value):
        assert isinstance(value, int), value

    test()


def test_canonical_reference_is_not_inlined(ctx):
    # One target, many pointers: the strategy is built once and shared, where inlining would
    # rebuild the same subtree for every pointer.
    definitions = {"text": {"type": "string", "minLength": 3}}
    properties = {f"p{index}": {"$ref": "#/$defs/text"} for index in range(20)}
    schema = {"type": "object", "properties": properties, "required": list(properties), "$defs": definitions}

    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)

    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=10, deadline=None)
    def test(value):
        assert is_valid(value), value

    test()


def test_canonical_reference_without_a_target_is_refused_by_canonicalization():
    schema = {"type": "object", "properties": {"a": {"$ref": "#/$defs/missing"}}, "$defs": {"other": {}}}

    with pytest.raises(jsonschema_rs.canonical.CanonicalizationError, match="does not exist"):
        jsonschema_rs.canonicalize(schema, draft=jsonschema_rs.Draft202012, pattern_options=FANCY_REGEX_OPTIONS)

    assert _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator) is None


def test_canonical_pattern_naming_a_character_outside_the_alphabet(ctx):
    # `from_regex` refuses a pattern that spells out a character the alphabet excludes, even where
    # the same pattern admits others it does not. Those values are still fair game.
    schema = {"type": "string", "pattern": "^[\\x85a-z]{1,10}$"}
    matches = re.compile("^[\x85a-z]{1,10}$").fullmatch

    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert matches(value), value

    test()

    find(built, lambda value: len(value) > 1, settings=settings(max_examples=1000, database=None))


def test_canonical_object_floor_over_values_that_cannot_be_drawn():
    # Every property would need a value, and the only schema on offer admits none.
    schema = {
        "type": "object",
        "additionalProperties": {"$ref": "#/$defs/node"},
        "minProperties": 1,
        "$defs": {"node": {"type": "object", "required": ["child"], "properties": {"child": {"$ref": "#/$defs/node"}}}},
    }
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    with pytest.raises(Unsatisfiable):
        find(built, lambda value: True, settings=settings(max_examples=50, database=None))


def test_canonical_reference_with_no_finite_value_admits_nothing():
    # Every instance would need a child, and so would that child.
    schema = {
        "$ref": "#/$defs/node",
        "$defs": {"node": {"type": "object", "required": ["child"], "properties": {"child": {"$ref": "#/$defs/node"}}}},
    }
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    with pytest.raises(Unsatisfiable):
        find(built, lambda value: True, settings=settings(max_examples=50, database=None))


UNSUPPORTED_SCHEMAS = [
    ({"type": "string", "contentMediaType": "application/json"}, jsonschema_rs.Draft7Validator),
    ({"type": "string", "contentEncoding": "base64"}, jsonschema_rs.Draft7Validator),
    # A pattern Python `re` rejects cannot drive generation, with or without a format.
    ({"type": "string", "pattern": r"\p{L}"}, jsonschema_rs.Draft202012Validator),
    ({"type": "string", "format": "uuid", "pattern": r"\p{L}"}, jsonschema_rs.Draft202012Validator),
    # Two formats at once needs a conjunction this module cannot build.
    (
        {"allOf": [{"type": "string", "format": "ipv4"}, {"type": "string", "format": "date"}]},
        jsonschema_rs.Draft202012Validator,
    ),
    # A node behind a pattern is only reached on a draw, and must still be refused up front.
    (
        {"type": "object", "patternProperties": {"^a": {"type": "string", "pattern": r"\p{L}"}}},
        jsonschema_rs.Draft202012Validator,
    ),
]


@pytest.mark.parametrize(
    ("schema", "validator_cls"), UNSUPPORTED_SCHEMAS, ids=[str(schema) for schema, _ in UNSUPPORTED_SCHEMAS]
)
def test_canonical_unsupported_schemas_fall_back(schema, validator_cls):
    assert _canonical_strategy_or_none(schema, GenerationConfig(), validator_cls) is None


def test_canonical_number_generation_without_representable_values():
    built = _canonical_strategy_or_none(
        {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 5e-324},
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    with pytest.raises(Unsatisfiable):
        find(built, lambda _: True, settings=settings(max_examples=10, database=None))


def test_canonical_integer_grid_without_a_multiple_in_range():
    # No integer between 1 and 2.5 is a multiple of 1.5, and canonicalization does not rule it out.
    schema = {"type": "integer", "minimum": 1, "maximum": 2.5, "multipleOf": 1.5}
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    with pytest.raises(Unsatisfiable):
        find(built, lambda _: True, settings=settings(max_examples=10, database=None))


@pytest.mark.parametrize(
    "body_schema",
    [
        {
            "type": "number",
            "multipleOf": 0.1,
            "exclusiveMinimum": 1e20,
            "maximum": 10**20 + 100_000,
        },
        {
            "anyOf": [
                {
                    "type": "number",
                    "multipleOf": 0.1,
                    "exclusiveMinimum": 1e20,
                    "maximum": 10**20 + 100_000,
                },
                {"const": "valid"},
            ]
        },
        # The bound folds onto a grid point no float holds, which the schema reads as its neighbour.
        {
            "type": "number",
            "multipleOf": 1e-20,
            "exclusiveMinimum": 10**25,
            "maximum": 1e308,
        },
    ],
    ids=["number", "number-branch", "number-bound-off-the-float-grid"],
)
def test_canonical_number_generation_respects_original_schema(ctx, body_schema):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": body_schema,
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    is_valid = jsonschema_rs.Draft202012Validator(body_schema).is_valid

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None)
    def test(case):
        assert is_valid(case.body)

    test()


@pytest.mark.parametrize(
    ("schema", "rejected"),
    [
        # The nearest float to the folded bound spells the excluded bound back.
        (
            {"type": "number", "multipleOf": 1e-20, "exclusiveMinimum": 10**25, "maximum": 1e308},
            1e25,
        ),
        # The nearest float to the maximum spells a decimal above it.
        (
            {"type": "number", "minimum": 0.1, "maximum": 123456789012345678901234567890},
            1.2345678901234568e29,
        ),
    ],
    ids=["bound-off-the-float-grid", "bound-above-the-float-grid"],
)
def test_canonical_number_never_spells_a_rejected_bound(schema, rejected):
    assert not jsonschema_rs.Draft202012Validator(schema).is_valid(rejected)

    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=100, deadline=None, database=None)
    def test(value):
        assert is_valid(value), value

    test()


def test_canonical_number_above_the_float_range():
    # No float clears the bound, but the integers above it are still JSON numbers.
    schema = {"type": "number", "exclusiveMinimum": sys.float_info.max}

    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=20, deadline=None, database=None)
    def test(value):
        assert isinstance(value, int)
        assert is_valid(value), value

    test()


def test_canonical_overlapping_pattern_properties():
    # `ab` is claimed by both patterns, so its value answers to both schemas at once.
    schema = {
        "type": "object",
        "patternProperties": {"^a": {"type": "integer", "multipleOf": 2}, "b$": {"minimum": 10}},
        "required": ["ab"],
    }
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert is_valid(value), value

    test()


def test_canonical_object_prefers_documented_keys_for_the_floor():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}, "minProperties": 2}
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    @given(built)
    @settings(max_examples=50, deadline=None)
    def test(value):
        assert {"a", "b"} <= set(value), value

    test()


CLOSED_OBJECT_SCHEMAS = [
    ({"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False}, {"a"}),
    (
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        },
        {"a", "b"},
    ),
    # Names the schema admits without constraining their values.
    ({"type": "object", "propertyNames": {"enum": ["a", "z"]}}, {"a", "z"}),
    ({"type": "object", "propertyNames": {"const": "a"}}, {"a"}),
    (
        {"type": "object", "properties": {"a": {"type": "integer"}}, "propertyNames": {"enum": ["a", "z"]}},
        {"a", "z"},
    ),
]


@pytest.mark.parametrize(
    ("schema", "allowed"),
    CLOSED_OBJECT_SCHEMAS,
    ids=["closed", "closed-with-required", "names-enum", "names-const", "names-beyond-properties"],
)
def test_canonical_closed_object_generation(schema, allowed):
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    is_valid = jsonschema_rs.validator_for(schema).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert set(value) <= allowed, value
        assert is_valid(value), value

    test()


def test_canonical_closed_object_reaches_every_admitted_name():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    find(built, lambda value: set(value) == {"a", "b"}, settings=settings(max_examples=1000, database=None))


def test_canonical_object_unsatisfiable_when_required_name_is_not_admitted():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["b"],
        "additionalProperties": False,
    }
    assert _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator) is None


def test_canonical_object_respects_alphabet():
    built = _canonical_strategy_or_none(
        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
        GenerationConfig(allow_x00=False, codec="ascii"),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        for key, item in value.items():
            assert "\x00" not in key
            key.encode("ascii")
            if isinstance(item, str):
                assert "\x00" not in item

    test()


@pytest.mark.parametrize(
    ("type_name", "admitted", "rejected"),
    [
        ("null", [None], [False, 0, "", [], {}]),
        ("boolean", [True, False], [None, 0, 1, "", [], {}]),
        # `True == 1` in Python, but `true` is not a JSON number.
        ("integer", [0, 1, -3], [None, True, False, 1.5, "1", [], {}]),
        ("number", [0, 1, -3, 1.5], [None, True, False, "1", [], {}]),
        ("string", ["", "a"], [None, True, 1, [], {}]),
        ("array", [[], [1]], [None, True, 1, "", {}]),
        ("object", [{}, {"a": 1}], [None, True, 1, "", []]),
    ],
)
def test_typed_group_checks(type_name, admitted, rejected):
    # Nothing downstream would notice this table being wrong: no canonical body admits a value
    # outside its own type, so the guard never fires. Pinned directly for that reason.
    check = strategy._TYPE_CHECKS[type_name]
    assert all(check(value) for value in admitted)
    assert not any(check(value) for value in rejected)


def test_canonical_typed_group_keeps_the_type():
    # `type` and the enum arrive as separate halves of one node: Draft 4 reads `enum` without a type
    # check, so the type has to be applied on top of whatever the body admits.
    schema = {"type": "integer", "oneOf": [{"enum": [1, "a"]}, {"enum": [2, None]}]}
    canonical_schema = jsonschema_rs.canonicalize(schema, draft=jsonschema_rs.Draft4)
    assert canonical_schema.kind == "typed_group"
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft4Validator)
    assert built is not None
    is_valid = jsonschema_rs.Draft4Validator(schema).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert is_valid(value), value

    test()

    find(built, lambda value: value == 2, settings=settings(max_examples=1000, database=None))


UNSATISFIABLE_ARRAY_SCHEMAS = [
    # Nothing clears the demand without matching it, and the ceiling admits fewer matches than
    # the lower size bound needs.
    {
        "type": "array",
        "items": {"type": "string", "pattern": "^a{5,}$"},
        "contains": {"minLength": 3},
        "maxContains": 1,
        "minItems": 3,
    },
    # Every position matches, so the ceiling and the lower size bound cannot both hold.
    {
        "type": "array",
        "items": {"type": "integer"},
        "contains": {"type": "integer"},
        "maxContains": 2,
        "minItems": 4,
    },
    # Two distinct values are demanded from a domain holding one.
    {"type": "array", "contains": {"const": 7}, "minContains": 2, "uniqueItems": True},
    # The demand admits two values, but only one of them clears `items`.
    {
        "type": "array",
        "items": {"type": "string"},
        "contains": {"enum": [1, "a"]},
        "minContains": 2,
        "uniqueItems": True,
    },
    # The pinned position and both demanded elements together overflow the length ceiling.
    {
        "type": "array",
        "prefixItems": [{"type": "integer"}],
        "items": {"type": "string"},
        "allOf": [{"contains": {"const": "A"}}, {"contains": {"const": "B"}}],
        "maxItems": 2,
    },
    # The tail runs out of distinct values below the floor, and the barred match cannot fill in.
    {
        "type": "array",
        "items": {"enum": [1, 2, 3]},
        "contains": {"const": 2},
        "minContains": 0,
        "maxContains": 0,
        "minItems": 3,
        "uniqueItems": True,
    },
    # The prefix stops where its values run out, leaving the demand nowhere to go.
    {
        "type": "array",
        "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [1, 2]}],
        "uniqueItems": True,
        "contains": {"const": 9},
    },
    # The prefix stops at two positions, and nothing may follow it to reach the third.
    {
        "type": "array",
        "prefixItems": [{"enum": [1, 2]}, {"enum": [1, 2]}, {"enum": [1, 2]}],
        "contains": {"type": "integer"},
        "minItems": 3,
        "uniqueItems": True,
    },
]


@pytest.mark.parametrize("schema", UNSATISFIABLE_ARRAY_SCHEMAS, ids=str)
def test_canonical_array_admits_nothing(schema):
    # Canonicalization cannot see these contradictions; laying the positions out does, and the answer
    # is a strategy that draws nothing.
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None
    assert built.is_empty


def test_canonical_array_min_contains_beyond_what_hypothesis_draws():
    # No looser strategy to filter, so this is reported against the operation. Finding that out must
    # not cost a strategy per demanded position — that would be gigabytes before the first draw.
    with pytest.raises(InvalidSchema, match="5000000 demanded elements"):
        _canonical_strategy_or_none(
            {"type": "array", "items": {"type": "integer"}, "contains": {"const": 7}, "minContains": 5000000},
            GenerationConfig(),
            jsonschema_rs.Draft202012Validator,
        )


def test_canonical_array_contains_is_satisfied_without_filtering():
    # The demanded element is placed, not filtered for, so a needle `items` rarely hits still lands.
    built = _canonical_strategy_or_none(
        {"type": "array", "items": {"type": "integer"}, "contains": {"const": 7654321}},
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert 7654321 in value, value

    test()


def test_canonical_array_min_contains_places_every_demanded_element():
    built = _canonical_strategy_or_none(
        {"type": "array", "items": {"type": "string"}, "contains": {"const": "A"}, "minContains": 3},
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert value.count("A") >= 3, value

    test()


def test_canonical_array_max_contains_uses_non_matching_filler():
    built = _canonical_strategy_or_none(
        {
            "type": "array",
            "items": {"type": "integer"},
            "contains": {"minimum": 10},
            "minContains": 2,
            "maxContains": 3,
            "minItems": 5,
        },
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert sum(item >= 10 for item in value) == 2, value

    test()


def test_canonical_array_unique_items_separates_booleans_from_numbers():
    # `true` and `1` are distinct JSON values, so both may sit in the same unique array.
    built = _canonical_strategy_or_none(
        {"type": "array", "items": True, "uniqueItems": True, "minItems": 2},
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    find(
        built,
        lambda value: any(item is True for item in value) and 1 in value,
        settings=settings(max_examples=2000, database=None),
    )


def test_canonical_array_unique_items_merges_equal_numbers():
    # `1` and `1.0` are the same JSON value and must never share an array.
    built = _canonical_strategy_or_none(
        {"type": "array", "items": {"type": "number"}, "uniqueItems": True, "minItems": 2, "maxItems": 5},
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    @given(built)
    @settings(max_examples=50, deadline=None)
    def test(value):
        rendered = [float(item) for item in value]
        assert len(set(rendered)) == len(rendered), value

    test()


def test_canonical_string_format_byte():
    built = _canonical_strategy_or_none(
        {"type": "string", "format": "byte"}, GenerationConfig(), jsonschema_rs.Draft4Validator
    )
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        b64decode(value)

    test()


def test_canonical_string_format_binary():
    built = _canonical_strategy_or_none(
        {"type": "string", "format": "binary"}, GenerationConfig(), jsonschema_rs.Draft4Validator
    )
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None)
    def test(value):
        assert isinstance(value, Binary), value

    test()


def test_canonical_format_drops_values_over_the_length_bound():
    # A format generator cannot be steered by length, so the bound can only be filtered for; the
    # generator reaches 32 characters here and every one of those must be discarded.
    schema = {"type": "string", "format": "date-time", "maxLength": 25}
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)
    assert built is not None

    @given(built)
    @settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    def test(value):
        assert len(value) <= 25, value

    test()


def test_canonical_format_drops_values_the_pattern_rejects():
    built = _canonical_strategy_or_none(
        {"type": "string", "format": "uuid", "pattern": "^not-a-uuid$"},
        GenerationConfig(),
        jsonschema_rs.Draft202012Validator,
    )
    assert built is not None

    with pytest.raises(Unsatisfiable):
        find(built, lambda _: True, settings=settings(max_examples=10, database=None))


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_canonical_object_property_formats(ctx, version):
    body_schema = {
        "type": "object",
        "properties": {"id": {"type": "string", "format": "uuid"}, "seen": {"type": "string", "format": "date-time"}},
        "required": ["id", "seen"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )
    is_valid = jsonschema_rs.Draft202012Validator(body_schema, validate_formats=True).is_valid

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None)
    def test(case):
        assert is_valid(case.body), case.body

    test()


def test_canonical_object_keeps_declared_names_outside_the_alphabet():
    # The alphabet governs generated strings; a name the schema mandates is not negotiable.
    schema = {"type": "object", "properties": {"é": {"type": "integer"}}, "required": ["é"]}
    built = _canonical_strategy_or_none(schema, GenerationConfig(codec="ascii"), jsonschema_rs.Draft202012Validator)
    assert built is not None

    @given(built)
    @settings(max_examples=10, deadline=None)
    def test(value):
        assert "é" in value, value

    test()


# Bounds where a decimal and its `f64` reading disagree.
NUMERIC_BOUNDS = st.sampled_from(
    [-(10**25), -1, 0, 1, 0.1, 2.5, 1e-7, 10**18 - 1, 1e18, 1e25, 10**25, 1e308, 10**400, 5e-324]
)
MULTIPLE_OF_VALUES = st.sampled_from([1, 2, 7, 0.1, 0.25, 1.5, 1e-7, 1e308])
STRING_PATTERNS = st.sampled_from(["^x", "x$", "^[a-z]{2,4}$", "\\d+", "[0-9]{2}", "^\\w+$", "a|b", "\\s"])
PROPERTY_NAMES = ["a", "b", "ax", "x1", "zz"]
# `^\d` and `^\w+$` read one way in Python and another in the validator's engine.
NAME_CONSTRAINTS = [
    {"enum": ["a", "zz"]},
    {"const": "a"},
    {"pattern": "^x"},
    {"pattern": "^\\d"},
    {"pattern": "^\\w+$"},
    {"maxLength": 3},
]
JSON_VALUES = [None, True, 1, 1.5, "a", "", [], [1], {}, {"a": 1}]


@st.composite
def numeric_schemas(draw):
    schema = {"type": draw(st.sampled_from(["integer", "number"]))}
    keywords = ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"]
    for keyword in draw(st.sets(st.sampled_from(keywords), max_size=3)):
        schema[keyword] = draw(MULTIPLE_OF_VALUES if keyword == "multipleOf" else NUMERIC_BOUNDS)
    # Canonicalization drops the exclusive bound when its inclusive partner is outside the float range.
    for inclusive, exclusive in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
        if abs(schema.get(inclusive, 0)) > sys.float_info.max:
            schema.pop(exclusive, None)
    return schema


@st.composite
def string_schemas(draw):
    schema = {"type": "string"}
    if draw(st.booleans()):
        # A format generator answers to no facet around it, so the bounds stay loose enough to survive.
        schema["format"] = draw(st.sampled_from(ASSERTED_FORMATS))
        if draw(st.booleans()):
            schema["maxLength"] = draw(st.integers(0, 40))
        if draw(st.booleans()):
            schema["minLength"] = draw(st.integers(0, 8))
        return schema
    if draw(st.booleans()):
        schema["pattern"] = draw(STRING_PATTERNS)
    if draw(st.booleans()):
        schema["minLength"] = draw(st.integers(0, 4))
    if draw(st.booleans()):
        schema["maxLength"] = draw(st.integers(0, 8))
    return schema


@st.composite
def object_schemas(draw, children):
    schema = {"type": "object"}
    names = sorted(draw(st.sets(st.sampled_from(PROPERTY_NAMES), max_size=3)))
    if names:
        schema["properties"] = {name: draw(children) for name in names}
        schema["required"] = sorted(draw(st.sets(st.sampled_from(names), max_size=2)))
    if draw(st.booleans()):
        schema["additionalProperties"] = draw(st.one_of(st.just(False), children))
    # Two patterns can claim one key, and its value has to satisfy both at once.
    patterns = sorted(draw(st.sets(st.sampled_from(["^a", "x$", "^\\d", "^.{2}$"]), max_size=2)))
    if patterns:
        schema["patternProperties"] = {pattern: draw(children) for pattern in patterns}
    if draw(st.booleans()):
        schema["propertyNames"] = draw(st.sampled_from(NAME_CONSTRAINTS))
    if draw(st.booleans()):
        schema["minProperties"] = draw(st.integers(0, 3))
    if draw(st.booleans()):
        schema["maxProperties"] = draw(st.integers(0, 4))
    return schema


@st.composite
def array_schemas(draw, children):
    schema = {"type": "array"}
    if draw(st.booleans()):
        schema["items"] = draw(st.one_of(st.just(False), children))
    if draw(st.booleans()):
        schema["prefixItems"] = draw(st.lists(children, min_size=1, max_size=2))
    unique = draw(st.booleans())
    if unique:
        schema["uniqueItems"] = True
    if draw(st.booleans()):
        # A floor above the distinct values an element admits leaves the draw spinning on uniqueness.
        schema["minItems"] = draw(st.integers(0, 2 if unique else 3))
    if draw(st.booleans()):
        schema["maxItems"] = draw(st.integers(0, 4))
    return schema


ANY_SCHEMA = st.recursive(
    st.one_of(
        st.sampled_from([{}, {"type": "null"}, {"type": "boolean"}]),
        numeric_schemas(),
        string_schemas(),
        st.builds(lambda value: {"const": value}, st.sampled_from(JSON_VALUES)),
        st.builds(
            lambda values: {"enum": values},
            st.lists(st.sampled_from(JSON_VALUES), min_size=1, max_size=4, unique_by=repr),
        ),
    ),
    lambda children: st.one_of(
        object_schemas(children),
        array_schemas(children),
        st.builds(lambda branches: {"anyOf": branches}, st.lists(children, min_size=1, max_size=3)),
        st.builds(lambda branches: {"allOf": branches}, st.lists(children, min_size=2, max_size=2)),
    ),
    max_leaves=4,
)


def assert_generation_is_sound(
    schemas, *, max_examples, floor, validator_cls=jsonschema_rs.Draft202012Validator, allow_unbuildable=False
):
    validated = 0

    @given(schema=schemas, data=st.data())
    @settings(
        max_examples=max_examples,
        deadline=None,
        database=None,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    def test(schema, data):
        nonlocal validated
        built = _canonical_strategy_or_none(schema, GenerationConfig(), validator_cls)
        # A schema this module declines to model is served by `hypothesis-jsonschema` instead.
        assume(built is not None)
        try:
            value = data.draw(built)
            is_valid = validator_cls(schema, validate_formats=True, pattern_options=FANCY_REGEX_OPTIONS).is_valid
        except InvalidArgument:
            # Hypothesis refuses a collection floor past its own buffer, whatever builds the strategy.
            if not allow_unbuildable:
                raise
            assume(False)
        except jsonschema_rs.ValidationError:
            # Nothing can say whether a value fits a schema the validator itself refuses to load.
            if not allow_unbuildable:
                raise
            assume(False)
        assert is_valid(value), (schema, value)
        validated += 1

    test()
    # Schemas that never reach the assertion would leave the run green and empty.
    assert validated > floor, validated


def test_canonical_generation_soundness():
    assert_generation_is_sound(ANY_SCHEMA, max_examples=300, floor=50)


def test_canonical_number_generation_soundness():
    assert_generation_is_sound(numeric_schemas(), max_examples=500, floor=100)


def test_canonical_string_generation_soundness():
    assert_generation_is_sound(string_schemas(), max_examples=500, floor=100)


def test_canonical_object_generation_soundness():
    assert_generation_is_sound(object_schemas(ANY_SCHEMA), max_examples=400, floor=50)


def test_canonical_array_generation_soundness():
    assert_generation_is_sound(array_schemas(ANY_SCHEMA), max_examples=300, floor=50)


METASCHEMAS = [
    (jsonschema_rs.Draft4Validator, jsonschema.Draft4Validator.META_SCHEMA),
    (jsonschema_rs.Draft6Validator, jsonschema.Draft6Validator.META_SCHEMA),
    (jsonschema_rs.Draft7Validator, jsonschema.Draft7Validator.META_SCHEMA),
]
METASCHEMA_IDS = ["draft4", "draft6", "draft7"]


@pytest.mark.parametrize(("validator_cls", "metaschema"), METASCHEMAS, ids=METASCHEMA_IDS)
def test_metaschema_generation(validator_cls, metaschema):
    built = _canonical_strategy_or_none(metaschema, GenerationConfig(), validator_cls)
    assert built is not None
    is_valid = validator_cls(metaschema).is_valid

    @given(built)
    @settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    def test(value):
        assert is_valid(value), value

    test()


@pytest.mark.parametrize(("validator_cls", "metaschema"), METASCHEMAS, ids=METASCHEMA_IDS)
def test_metaschema_generation_soundness(validator_cls, metaschema):
    schemas = _canonical_strategy_or_none(metaschema, GenerationConfig(), validator_cls)
    assert schemas is not None
    # 250: metaschema draws are mostly shallow, so a smaller budget leaves the 50-validation floor flaky.
    assert_generation_is_sound(schemas, max_examples=250, floor=50, validator_cls=validator_cls, allow_unbuildable=True)
