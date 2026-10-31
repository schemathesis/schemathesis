import json
import re
import sys
import threading

import pytest
import yaml
from flask import jsonify

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.config._auth import DynamicTokenAuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.core.failures import AcceptedNegativeData, Failure, FailureGroup, MalformedJson
from schemathesis.core.mutations import MutationMetadata, OperatorKind
from schemathesis.core.parameters import EncodedPath, ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import Response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoveragePhaseData,
    CoverageScenario,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    StatefulPhaseData,
    TestPhase,
)
from schemathesis.generation.overrides import Override
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
    ContentDeserializationError,
    DiscriminatorMismatch,
    JsonSchemaError,
    MissingHeaderNotRejected,
    RejectedPositiveData,
    UnsupportedMethodResponse,
    UseAfterFree,
)
from schemathesis.specs.openapi.checks import (
    ResourcePath,
    _additional_properties_hint,
    _body_negation_becomes_valid_after_serialization,
    _is_prefix_operation,
    allow_header_conformance,
    content_type_conformance,
    has_only_additional_properties_in_non_body_parameters,
    missing_required_header,
    negative_data_rejection,
    positive_data_acceptance,
    response_schema_conformance,
    unsupported_method,
    use_after_free,
)
from schemathesis.specs.openapi.negative.mutations import Mutation, MutationChannel
from schemathesis.specs.openapi.validation import _check_discriminator
from test.utils import check_context


@pytest.mark.parametrize(
    ("lhs", "lhs_vars", "rhs", "rhs_vars", "expected"),
    [
        # Exact match, no variables
        ("/users/123", {}, "/users/123", {}, True),
        # Different paths, no variables
        ("/users/123", {}, "/users/456", {}, False),
        # Different variable names
        ("/users/{id}", {"id": "123"}, "/users/{user_id}", {"user_id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{user_id}", {"user_id": "456"}, False),
        # Singular vs. plural
        ("/user/{id}", {"id": "123"}, "/users/{id}", {"id": "123"}, True),
        ("/user/{id}", {"id": "123"}, "/users/{id}", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/user/{id}", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/user/{id}", {"id": "456"}, False),
        # Trailing slashes
        ("/users/{id}/", {"id": "123"}, "/users/{id}", {"id": "123"}, True),
        ("/users/{id}/", {"id": "123"}, "/users/{id}", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{id}/", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/", {"id": "456"}, False),
        ("/users/", {}, "/users", {}, True),
        ("/users", {}, "/users/", {}, True),
        # Empty paths
        ("", {}, "", {}, True),
        ("", {}, "/", {}, True),
        ("/", {}, "", {}, True),
        # Mismatched paths
        ("/users/{id}", {"id": "123"}, "/products/{id}", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{name}", {"name": "John"}, False),
        # LHS is a prefix of RHS
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
        # LHS is a prefix of RHS, with different number of variables
        ("/users/{id}", {"id": "123"}, "/users/{id}/{name}", {"id": "123", "name": "John"}, True),
        (
            "/users/{id}",
            {"id": "123"},
            "/users/{id}/{name}/{email}",
            {"id": "123", "name": "John", "email": "john@example.com"},
            True,
        ),
        # LHS is a prefix of RHS, with different variable values
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "123"}, True),
        # LHS is a prefix of RHS, with different variable types
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": 123}, True),
        ("/users/{id}", {"id": 123}, "/users/{id}/details", {"id": "123"}, True),
        # LHS is a prefix of RHS, with extra path segments
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view/edit", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view/edit", {"id": "456"}, False),
        # Longer than a prefix
        ("/one/two/three/four/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
    ],
)
def test_is_prefix_operation(lhs, lhs_vars, rhs, rhs_vars, expected):
    assert _is_prefix_operation(ResourcePath(lhs, lhs_vars), ResourcePath(rhs, rhs_vars)) == expected


def build_metadata(
    path_parameters=None,
    query=None,
    headers=None,
    cookies=None,
    body=None,
    generation_modes=(GenerationMode.POSITIVE,),
    description="",
    parameter=None,
    parameter_location=None,
    location=None,
    mutations=None,
    phase=TestPhase.FUZZING,
):
    # When the test pins a type-mutation description, also populate the structured
    # Mutation record so the case carries what the engine produces for the same case.
    if mutations is None:
        mutations = ()
        if description.startswith("Invalid type") and parameter is not None:
            mutations = (
                Mutation(
                    path=(parameter,),
                    parameter_location=parameter_location or ParameterLocation.QUERY,
                    schema_pointer=f"/properties/{parameter}",
                    channel=MutationChannel.SCHEMA,
                    operator=OperatorKind.CHANGE_TYPE,
                    keywords=("type",),
                    parameter=parameter,
                    original_value=None,
                    new_value=None,
                ),
            )
    elif not description:
        # The engine derives the description from the mutations; mirror that instead of restating it.
        description = MutationMetadata(mutations=mutations).description
    return CaseMetadata(
        generation=GenerationInfo(
            time=0.1,
            mode=generation_modes[0],
        ),
        components={
            kind: ComponentInfo(mode=value)
            for kind, value in [
                (ParameterLocation.QUERY, query),
                (ParameterLocation.PATH, path_parameters),
                (ParameterLocation.HEADER, headers),
                (ParameterLocation.COOKIE, cookies),
                (ParameterLocation.BODY, body),
            ]
            if value is not None
        },
        phase=PhaseInfo(
            name=phase,
            data=(StatefulPhaseData if phase == TestPhase.STATEFUL else FuzzingPhaseData)(
                description=description,
                parameter=parameter,
                parameter_location=parameter_location,
                location=location,
                mutations=mutations,
            ),
        ),
    )


def _assert_rejection(case, response, *, reported, context=None):
    context = check_context() if context is None else context
    if reported:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(context, response, case)
    else:
        assert negative_data_rejection(context, response, case) is None


def sample_paths():
    return {
        "/test": {
            "post": {
                "parameters": [
                    {
                        "in": "query",
                        "name": "key",
                        "schema": {"type": "integer", "minimum": 5},
                    },
                    {
                        "in": "header",
                        "name": "X-Key",
                        "schema": {"type": "integer", "minimum": 5},
                    },
                ]
            }
        }
    }


@pytest.fixture
def sample_raw_schema(ctx):
    return ctx.openapi.build_schema(sample_paths())


@pytest.fixture
def sample_schema(ctx):
    return ctx.openapi.load_schema(sample_paths())


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, False),
        (
            {"_meta": build_metadata(body=GenerationMode.NEGATIVE)},
            False,
        ),
        (
            {
                "query": {"key": 1},
                "_meta": build_metadata(query=GenerationMode.NEGATIVE),
            },
            False,
        ),
        (
            {
                "query": {"key": 1},
                "headers": {"X-Key": 42},
                "_meta": build_metadata(query=GenerationMode.NEGATIVE),
            },
            False,
        ),
        (
            {
                "query": {"key": 5, "unknown": 3},
                "_meta": build_metadata(query=GenerationMode.NEGATIVE),
            },
            True,
        ),
        (
            {
                "query": {"key": 5, "unknown": 3},
                "headers": {"X-Key": 42},
                "_meta": build_metadata(query=GenerationMode.NEGATIVE),
            },
            True,
        ),
    ],
)
def test_has_only_additional_properties_in_non_body_parameters(sample_schema, kwargs, expected):
    operation = sample_schema["/test"]["POST"]
    case = operation.Case(**kwargs)
    assert has_only_additional_properties_in_non_body_parameters(case) is expected


def _mutation(operator, keywords, parameter=None, location=ParameterLocation.QUERY):
    return Mutation(
        path=(parameter,) if parameter else (),
        parameter_location=location,
        schema_pointer=f"/properties/{parameter}" if parameter else "",
        channel=MutationChannel.SCHEMA if operator == OperatorKind.NEGATE_CONSTRAINTS else MutationChannel.VALUE,
        operator=operator,
        keywords=tuple(keywords),
        parameter=parameter,
        original_value=None,
        new_value=None,
    )


_ADDITIONAL_PROPERTIES_MUTATION = _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("additionalProperties",))
_BODY_MIN_LENGTH_MUTATION = _mutation(OperatorKind.VALUE_VIOLATOR, ("minLength",), parameter="field")
_PATH_PATTERN_MUTATION = _mutation(OperatorKind.VALUE_VIOLATOR, ("pattern",), parameter="id")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param(
            {
                "query": {"key": 5, "unknown": 3},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    path_parameters=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    parameter_location=ParameterLocation.QUERY,
                    mutations=(_ADDITIONAL_PROPERTIES_MUTATION,),
                ),
            },
            True,
            id="phantom-path-negation-suppresses",
        ),
        # Issue #3730 reproducer: engine adds a nameless query param (`?=val`).
        pytest.param(
            {
                "query": {"key": 5, "": "0"},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    path_parameters=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    parameter_location=ParameterLocation.QUERY,
                    mutations=(_ADDITIONAL_PROPERTIES_MUTATION,),
                ),
            },
            True,
            id="empty-name-query-extra-suppresses",
        ),
        pytest.param(
            {
                "query": {"key": 5, "unknown": 3},
                "_meta": build_metadata(
                    body=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    parameter_location=ParameterLocation.BODY,
                    mutations=(_BODY_MIN_LENGTH_MUTATION,),
                ),
            },
            False,
            id="real-body-mutation-still-denies",
        ),
        pytest.param(
            {
                "query": {"key": 5, "unknown": 3},
                "_meta": build_metadata(
                    path_parameters=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    parameter_location=ParameterLocation.PATH,
                    mutations=(_PATH_PATTERN_MUTATION,),
                ),
            },
            False,
            id="real-path-mutation-still-denies",
        ),
        # Mutating several locations leaves no single targeted location on the case.
        pytest.param(
            {
                "query": {"key": 5, "": "null"},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    path_parameters=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    mutations=(
                        _ADDITIONAL_PROPERTIES_MUTATION,
                        _mutation(
                            OperatorKind.NEGATE_CONSTRAINTS,
                            ("pattern",),
                            parameter="id",
                            location=ParameterLocation.PATH,
                        ),
                    ),
                ),
            },
            False,
            id="path-mutation-beside-query-extra-denies",
        ),
    ],
)
def test_has_only_additional_properties_mutations_aware(sample_schema, kwargs, expected):
    case = sample_schema["/test"]["POST"].Case(**kwargs)
    assert has_only_additional_properties_in_non_body_parameters(case) is expected


def _coverage_negative_metadata(parameter, location):
    return CaseMetadata(
        generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
        components={location: ComponentInfo(mode=GenerationMode.NEGATIVE)},
        phase=PhaseInfo(
            name=TestPhase.COVERAGE,
            data=CoveragePhaseData(
                scenario=CoverageScenario.INCORRECT_TYPE,
                description="Incorrect type",
                location=location.value,
                parameter=parameter,
                parameter_location=location,
            ),
        ),
    )


_TAG_AND_LIMIT_PARAMETERS = [
    {"in": "query", "name": "tag", "schema": {"type": "array", "items": {"type": "string"}}},
    {"in": "query", "name": "limit", "schema": {"type": "integer"}},
    {"in": "header", "name": "X-Limit", "schema": {"type": "integer"}},
]


@pytest.mark.parametrize(
    ("parameters", "kwargs", "expected"),
    [
        pytest.param(
            _TAG_AND_LIMIT_PARAMETERS,
            {
                "query": {"tag": ["a"], "limit": "abc"},
                "_meta": _coverage_negative_metadata("limit", ParameterLocation.QUERY),
            },
            False,
            id="coverage-scalar-beside-array",
        ),
        pytest.param(
            _TAG_AND_LIMIT_PARAMETERS,
            {
                "query": {"tag": "abc", "limit": 1},
                "_meta": _coverage_negative_metadata("tag", ParameterLocation.QUERY),
            },
            True,
            id="coverage-array",
        ),
        pytest.param(
            _TAG_AND_LIMIT_PARAMETERS,
            {
                "query": {"tag": ["a"], "unknown": "x"},
                "headers": {"X-Limit": "abc"},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    headers=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    mutations=(
                        _ADDITIONAL_PROPERTIES_MUTATION,
                        _mutation(OperatorKind.CHANGE_TYPE, ("type",), "X-Limit", ParameterLocation.HEADER),
                    ),
                ),
            },
            False,
            id="invalid-header-after-array-query",
        ),
        pytest.param(
            [{"in": "query", "name": "title", "schema": {"type": "string"}}],
            {
                "query": {"title": "null", "unknown": "x"},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    mutations=(_ADDITIONAL_PROPERTIES_MUTATION,),
                ),
            },
            True,
            id="null-text-for-string",
        ),
        pytest.param(
            [{"in": "query", "name": "page", "schema": {"type": "integer", "nullable": True}}],
            {
                "query": {"page": "null", "unknown": "x"},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    mutations=(_ADDITIONAL_PROPERTIES_MUTATION,),
                ),
            },
            True,
            id="null-text-for-nullable-integer",
        ),
        pytest.param(
            _TAG_AND_LIMIT_PARAMETERS,
            {
                "query": {"tag": []},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    mutations=(_mutation(OperatorKind.NEGATE_CONSTRAINTS, ("required",), "tag"),),
                ),
            },
            True,
            id="required-array-sent-as-nothing",
        ),
        pytest.param(
            [{"in": "query", "name": "filter", "schema": {"type": "object"}}],
            {
                "query": {"filter": "abc"},
                "_meta": build_metadata(
                    query=GenerationMode.NEGATIVE,
                    generation_modes=[GenerationMode.NEGATIVE],
                    mutations=(_mutation(OperatorKind.CHANGE_TYPE, ("type",), "filter"),),
                ),
            },
            True,
            id="object-type-mutation",
        ),
    ],
)
def test_has_only_additional_properties_reads_parameters_as_sent(ctx, parameters, kwargs, expected):
    operation = ctx.openapi.load_operation(
        "GET /tags", {"parameters": parameters, "responses": {"200": {"description": "OK"}}}
    )
    assert has_only_additional_properties_in_non_body_parameters(operation.Case(**kwargs)) is expected


def test_has_only_additional_properties_with_large_quantifier_pattern(ctx):
    # Patterns with large quantifiers require pattern_options with sufficient size_limit
    operation = ctx.openapi.load_operation(
        "POST /test",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "key",
                    "schema": {
                        "type": "string",
                        "pattern": "^.{1,262144}$",
                    },
                },
            ]
        },
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE),
        query={"key": "valid", "unknown": "extra"},
    )
    # Should not raise - the validator should handle patterns with large quantifiers
    assert has_only_additional_properties_in_non_body_parameters(case) is True


def _boolean_parameter_case(ctx, location, value):
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "parameters": [
                {"in": location.value, "name": "flag", "required": True, "schema": {"type": "boolean"}},
            ]
        },
    )
    return operation.Case(
        _meta=build_metadata(
            generation_modes=[GenerationMode.NEGATIVE],
            parameter_location=location,
            mutations=(_mutation(OperatorKind.NEGATE_CONSTRAINTS, ("additionalProperties",), location=location),),
            **{location.container_name: GenerationMode.NEGATIVE},
        ),
        **{location.container_name: {"flag": value, "unknown": "junk"}},
    )


_BOOLEAN_PARAMETER_LOCATIONS = [ParameterLocation.QUERY, ParameterLocation.HEADER, ParameterLocation.COOKIE]


@pytest.mark.parametrize("location", _BOOLEAN_PARAMETER_LOCATIONS, ids=["query", "header", "cookie"])
def test_negative_data_rejection_ignores_extras_next_to_boolean_wire_value(ctx, response_factory, location):
    # Booleans reach the check already spelled as the wire sends them.
    case = _boolean_parameter_case(ctx, location, "false")
    assert has_only_additional_properties_in_non_body_parameters(case) is True
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


@pytest.mark.parametrize("location", _BOOLEAN_PARAMETER_LOCATIONS, ids=["query", "header", "cookie"])
def test_negative_data_rejection_reports_invalid_boolean_next_to_extras(ctx, response_factory, location):
    case = _boolean_parameter_case(ctx, location, "maybe")
    assert has_only_additional_properties_in_non_body_parameters(case) is False
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


def test_negative_data_rejection_ignores_extras_next_to_allow_empty_value_string(ctx, response_factory):
    # `allowEmptyValue` admits types other than string, but "0" is still a valid string, not `false`.
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "parameters": [
                {"in": "query", "name": "title", "allowEmptyValue": True, "schema": {"type": "string"}},
            ]
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter_location=ParameterLocation.QUERY,
            mutations=(_ADDITIONAL_PROPERTIES_MUTATION,),
        ),
        query={"title": "0", "unknown": "null"},
    )
    assert has_only_additional_properties_in_non_body_parameters(case) is True
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def _array_query_case(ctx, query, mutation, phase):
    operation = ctx.openapi.load_operation(
        "GET /tags",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "tag",
                    "required": True,
                    "style": "form",
                    "explode": True,
                    "schema": {"type": "array", "items": {"type": "string"}, "maxItems": 1},
                },
            ]
        },
    )
    return operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter=mutation.parameter,
            parameter_location=ParameterLocation.QUERY,
            mutations=(mutation,),
            phase=phase,
        ),
        query=query,
    )


_PHASES = pytest.mark.parametrize("phase", [TestPhase.FUZZING, TestPhase.STATEFUL], ids=["fuzzing", "stateful"])


@_PHASES
def test_negative_data_rejection_reports_negated_array_query_parameter(ctx, response_factory, phase):
    case = _array_query_case(
        ctx, {"tag": ["", ""]}, _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("maxItems",), "tag"), phase
    )
    assert has_only_additional_properties_in_non_body_parameters(case) is False
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


@_PHASES
def test_negative_data_rejection_ignores_extras_next_to_array_query_parameter(ctx, response_factory, phase):
    case = _array_query_case(ctx, {"tag": ["a"], "unknown": "junk"}, _ADDITIONAL_PROPERTIES_MUTATION, phase)
    assert has_only_additional_properties_in_non_body_parameters(case) is True
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_reports_scalar_query_mutation_beside_array_parameter(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /tags",
        {
            "parameters": [
                {"in": "query", "name": "tag", "schema": {"type": "array", "items": {"type": "string"}}},
                {"in": "query", "name": "limit", "schema": {"type": "integer"}},
            ]
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter="limit",
            parameter_location=ParameterLocation.QUERY,
            mutations=(_mutation(OperatorKind.CHANGE_TYPE, ("type",), "limit"),),
        ),
        query={"tag": ["a"], "limit": "abc"},
    )
    with pytest.raises(
        AcceptedNegativeData, match=r"parameter `limit` in query - violates `type` at /properties/limit"
    ):
        negative_data_rejection(check_context(), response_factory.requests(), case)


def _opaque_rejection(response_factory):
    # A rejection with nothing to attribute, so the hint falls back to its schema-side reasoning.
    return Response.from_requests(response_factory.requests(status_code=400), verify=True)


@pytest.mark.parametrize(
    ("body", "summary"),
    [
        ({"a": "x", "e1": 1}, "1 additional property not defined in the schema (`e1`)"),
        ({"e1": 1, "e2": 1, "e3": 1}, "3 additional properties not defined in the schema (`e1`, `e2`, `e3`)"),
        (
            {"e1": 1, "e2": 1, "e3": 1, "e4": 1},
            "4 additional properties not defined in the schema (`e1`, `e2`, `e3` and 1 more)",
        ),
        (
            {"e1": 1, "e2": 1, "e3": 1, "e4": 1, "e5": 1},
            "5 additional properties not defined in the schema (`e1`, `e2`, `e3` and 2 more)",
        ),
        ({"data": b"\x89PNG", "e1": 1}, None),
        ({"a": "x\x00", "e1": 1}, None),
        ({"tags": ["ok", "bad\x00"], "e1": 1}, None),
    ],
    ids=["one", "three", "four", "five", "binary-field", "nul-in-value", "nul-in-nested-list"],
)
def test_additional_properties_hint_names_extras(ctx, response_factory, body, summary):
    operation = ctx.openapi.load_operation(
        "POST /foo",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                                "data": {"type": "string", "format": "binary"},
                            },
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(body=body, media_type="application/json")
    expected = None
    if summary is not None:
        expected = (
            f"\nHint: The request body contains {summary}. The server appears to reject properties the schema allows. "
            "Declare `additionalProperties: false` if extras are not accepted, or make the server ignore unknown fields."
        )
    assert _additional_properties_hint(case, _opaque_rejection(response_factory)) == expected


@pytest.mark.parametrize(
    ("body", "expected_hint"),
    [
        pytest.param({"a": 1, "b": {"x": "q"}}, None, id="declared-keys-only"),
        pytest.param({"a": 1, "b": {"x": "q"}, "extra": "yes"}, "`extra`", id="real-extra-fires"),
    ],
)
def test_additional_properties_hint_resolves_bundled_ref(ctx, response_factory, body, expected_hint):
    # Bundled `$ref` bodies must be resolved before classifying extras.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "X", "version": "1"},
            "paths": {
                "/foo": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Foo"}}},
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "Foo": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"$ref": "#/components/schemas/Bar"},
                        },
                    },
                    "Bar": {"type": "object", "properties": {"x": {"type": "string"}}},
                },
            },
        }
    )
    operation = schema["/foo"]["POST"]
    case = operation.Case(body=body, media_type="application/json", method="POST")
    hint = _additional_properties_hint(case, _opaque_rejection(response_factory))
    if expected_hint is None:
        assert hint is None, f"False positive: {hint!r}"
    else:
        assert hint is not None and expected_hint in hint, f"Expected mention of {expected_hint} in {hint!r}"


@pytest.mark.parametrize("combinator", ["allOf", "anyOf"])
@pytest.mark.parametrize(
    ("body", "expected_hint"),
    [
        pytest.param({"a": "x", "b": "y"}, None, id="declared-keys-only"),
        pytest.param({"a": "x", "b": "y", "extra": "yes"}, "`extra`", id="real-extra-fires"),
    ],
)
def test_additional_properties_hint_with_composed_properties(ctx, response_factory, combinator, body, expected_hint):
    # Properties declared inside combinator branches are not extras, and telling the user to add
    # `additionalProperties: false` there would reject valid requests.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "X", "version": "1"},
            "paths": {
                "/foo": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        combinator: [
                                            {"$ref": "#/components/schemas/Base"},
                                            {"type": "object", "properties": {"a": {"type": "string"}}},
                                        ]
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "components": {"schemas": {"Base": {"type": "object", "properties": {"b": {"type": "string"}}}}},
        }
    )
    operation = schema["/foo"]["POST"]
    case = operation.Case(body=body, media_type="application/json", method="POST")
    hint = _additional_properties_hint(case, _opaque_rejection(response_factory))
    if expected_hint is None:
        assert hint is None, f"False positive: {hint!r}"
    else:
        assert hint is not None and expected_hint in hint, f"Expected mention of {expected_hint} in {hint!r}"
        assert "`a`" not in hint and "`b`" not in hint, f"Declared keys reported as extras: {hint!r}"


def test_additional_properties_hint_with_one_of_behind_chained_refs(ctx, response_factory):
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "X", "version": "1"},
            "paths": {
                "/foo": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "oneOf": [
                                            {"$ref": "#/components/schemas/Alias"},
                                            {
                                                "type": "object",
                                                "properties": {"a": {"type": "string"}},
                                                "required": ["a"],
                                            },
                                        ]
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
                    "Alias": {"$ref": "#/components/schemas/Base"},
                    "Base": {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]},
                }
            },
        }
    )
    case = schema["/foo"]["POST"].Case(body={"a": "x", "extra": "yes"}, media_type="application/json")
    assert _additional_properties_hint(case, _opaque_rejection(response_factory)) == _EXTRA_PROPERTY_HINT


def test_additional_properties_hint_skipped_when_branch_forbids_extras(ctx, response_factory):
    # `additionalProperties: false` inside a branch already forbids extras, so advising to add it is noise.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "X", "version": "1"},
            "paths": {
                "/foo": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "allOf": [
                                            {
                                                "type": "object",
                                                "properties": {"a": {"type": "string"}},
                                                "additionalProperties": False,
                                            }
                                        ]
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )
    operation = schema["/foo"]["POST"]
    case = operation.Case(body={"a": "x", "extra": "yes"}, media_type="application/json", method="POST")
    assert _additional_properties_hint(case, _opaque_rejection(response_factory)) is None


_EXTRA_PROPERTY_HINT = (
    "\nHint: The request body contains 1 additional property not defined in the schema (`extra`). "
    "The server appears to reject properties the schema allows. Declare `additionalProperties: false` if extras are "
    "not accepted, or make the server ignore unknown fields."
)


@pytest.mark.parametrize(
    ("error_body", "hint"),
    [
        pytest.param(b'{"name": ["This field may not be blank."]}', "", id="declared-field-blamed"),
        pytest.param(
            b'{"detail":[{"msg":"Input should be a valid string","loc":["parsed_body","name"],"type":"value_error"}]}',
            "",
            id="declared-field-blamed-by-flattened-pydantic-error",
        ),
        pytest.param(b'{"extra": ["This field may not be blank."]}', _EXTRA_PROPERTY_HINT, id="extra-property-blamed"),
        pytest.param(b'{"detail": "Bad request"}', _EXTRA_PROPERTY_HINT, id="no-field-blamed"),
    ],
)
def test_additional_properties_hint_follows_response_attribution(ctx, response_factory, error_body, hint):
    # Blaming extras for a rejection the server pinned on a declared field points the reader at the wrong fix.
    operation = ctx.openapi.load_operation(
        "POST /foo",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(body={"name": "value", "extra": "yes"}, media_type="application/json", _meta=build_metadata())
    response = Response.from_requests(response_factory.requests(status_code=400, content=error_body), verify=True)
    with pytest.raises(RejectedPositiveData) as exc:
        positive_data_acceptance(check_context(), response, case)
    assert (
        exc.value.message
        == f"Valid data should have been accepted\nExpected: 2xx, 3xx, 401, 403, 404, 409, 429, 5xx{hint}"
    )


@pytest.mark.parametrize(
    ("status_code", "reported"),
    [
        pytest.param(405, False, id="405-method-not-allowed-passes"),
        # 409 short-circuits validation on uniqueness-gated endpoints (duplicate email etc.)
        # before the server reaches the mutated field — treating it as "accepted" is a false positive.
        pytest.param(409, False, id="409-conflict-passes"),
        # A throttle refuses the request without judging its data.
        pytest.param(429, False, id="429-too-many-requests-passes"),
        pytest.param(200, True, id="200-still-flagged"),
    ],
)
def test_negative_data_rejection_passes_for_rejection_status_codes(
    response_factory, sample_schema, status_code, reported
):
    response = response_factory.requests(status_code=status_code)
    operation = sample_schema["/test"]["POST"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query={"key": 1},
    )
    _assert_rejection(case, response, reported=reported)


def test_negative_data_rejection_on_additional_properties(response_factory, sample_schema):
    # See GH-2312
    response = response_factory.requests()
    operation = sample_schema["/test"]["POST"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query={"key": 5, "unknown": 3},
    )
    assert (
        negative_data_rejection(
            check_context(),
            response,
            case,
        )
        is None
    )


_QUERY_TYPE_MUTATION = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="key")
_HEADER_KEY_MUTATION = _mutation(
    OperatorKind.NEGATE_CONSTRAINTS, ("minimum",), parameter="X-Key", location=ParameterLocation.HEADER
)


def test_negative_data_rejection_ignores_extra_headers_when_query_value_is_wire_identical(
    response_factory, sample_schema
):
    # `?key=7` is the same bytes a valid request sends, and unknown headers are ignored by servers.
    operation = sample_schema["/test"]["POST"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="violates `type` at /properties/key (was integer, became string)",
            parameter="key",
            parameter_location=ParameterLocation.QUERY,
            mutations=(_QUERY_TYPE_MUTATION,),
        ),
        query={"key": "7"},
        headers={"X-Key": 7, "X-Extra": "junk"},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def _wire_identical_case(schema, other_header):
    operation = schema["/test"]["POST"]
    return operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter="key",
            parameter_location=ParameterLocation.QUERY,
            mutations=(_QUERY_TYPE_MUTATION,),
        ),
        query={"key": "7"},
        headers=other_header,
    )


def _wire_identical_schema(ctx, header_schema):
    return ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {"in": "query", "name": "key", "schema": {"type": "integer", "minimum": 5}},
                        {"in": "header", "name": "X-Other", "schema": header_schema},
                    ]
                }
            }
        }
    )


def test_negative_data_rejection_keeps_checking_when_another_location_sends_an_array(ctx, response_factory):
    # Repeated keys carry arrays, so what the server read back cannot be compared to the schema.
    schema = _wire_identical_schema(ctx, {"type": "array", "items": {"type": "string"}})
    case = _wire_identical_case(schema, {"X-Other": ["a"]})
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


def test_negative_data_rejection_keeps_checking_when_another_location_has_an_unreadable_schema(ctx, response_factory):
    # `multipleOf: 0` builds no validator, so that location's values cannot be cleared.
    schema = _wire_identical_schema(ctx, {"type": "integer", "multipleOf": 0})
    case = _wire_identical_case(schema, {"X-Other": 7})
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


def test_negative_data_rejection_ignores_repeated_query_value_with_unreadable_schema(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items/{id}",
        {
            "parameters": [
                {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}},
                {"in": "query", "name": "key", "schema": {"type": "integer", "multipleOf": 0}},
            ]
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        path_parameters={"id": "abc"},
        query={"key": [7, 8]},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_names_no_parameters_when_several_locations_are_negated(
    response_factory, sample_schema
):
    operation = sample_schema["/test"]["POST"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=(_QUERY_TYPE_MUTATION, _HEADER_KEY_MUTATION),
        ),
        query={"key": 1},
        headers={"X-Key": 1},
    )
    with pytest.raises(AcceptedNegativeData) as exc:
        negative_data_rejection(check_context(), response_factory.requests(), case)
    assert "- query: violates `type` at /properties/key" in exc.value.message
    assert "- header: violates `minimum` at /properties/X-Key" in exc.value.message
    assert "parameters" not in exc.value.message


def test_negative_data_rejection_ignores_headers_on_operation_without_header_parameters(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items/{id}",
        {"parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "string"}}]},
    )
    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        path_parameters={"id": "abc"},
        headers={"X-Extra": "1"},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


@pytest.fixture
def path_and_query_schema(ctx):
    return ctx.openapi.load_schema(
        {
            "/items/{id}": {
                "get": {
                    "parameters": [
                        {"in": "path", "name": "id", "required": True, "schema": {"type": "integer", "minimum": 1}},
                        {"in": "query", "name": "key", "required": True, "schema": {"type": "integer", "minimum": 1}},
                        {"in": "header", "name": "X-Key", "required": True, "schema": {"type": "integer"}},
                    ]
                }
            }
        }
    )


def _path_and_query_case(schema, path_parameters, query, headers, mutations):
    meta = build_metadata(generation_modes=[GenerationMode.NEGATIVE], mutations=mutations)
    meta.components = {
        mutation.parameter_location: ComponentInfo(mode=GenerationMode.NEGATIVE) for mutation in mutations
    }
    return schema["/items/{id}"]["GET"].Case(
        _meta=meta,
        path_parameters=path_parameters,
        query=query,
        headers=headers,
    )


NULLABLE_BOOLEAN = {"type": "boolean", "nullable": True}


@pytest.mark.parametrize(
    ("version", "parameters", "query", "reported"),
    [
        ("3.0.2", {"flag": {"schema": NULLABLE_BOOLEAN, "allowEmptyValue": True}}, {"flag": "abc"}, True),
        ("3.0.2", {"flag": {"schema": NULLABLE_BOOLEAN}}, {"flag": "abc"}, True),
        ("3.0.2", {"flag": {"schema": {"type": "boolean"}, "allowEmptyValue": True}}, {"flag": "abc"}, True),
        ("3.1.0", {"flag": {"schema": {"type": ["boolean", "null"]}, "allowEmptyValue": True}}, {"flag": "abc"}, True),
        ("3.0.2", {"flag": {"schema": {"type": "boolean"}}}, {"flag": "abc"}, True),
        (
            "3.0.2",
            {
                "compressed": {"schema": NULLABLE_BOOLEAN, "allowEmptyValue": True},
                "full_size": {"schema": {**NULLABLE_BOOLEAN, "default": False}, "allowEmptyValue": True},
            },
            {"compressed": "abc", "full_size": "xyz"},
            True,
        ),
        ("3.0.2", {"flag": {"schema": NULLABLE_BOOLEAN, "allowEmptyValue": True}}, {"flag": "true"}, False),
        ("3.0.2", {"ids": {"schema": {"type": "array", "items": {"type": "integer"}}}}, {"ids": "5"}, False),
        (
            "3.0.2",
            {
                "ids": {"schema": {"type": "array", "items": {"type": "integer"}}},
                "flag": {"schema": NULLABLE_BOOLEAN, "allowEmptyValue": True},
            },
            {"ids": "5", "flag": "abc"},
            True,
        ),
        ("3.1.0", {"mode": {"schema": {"const": "on"}}}, {"mode": "abc"}, True),
        ("3.0.2", {"anything": {"schema": {}}}, {"anything": "abc"}, False),
    ],
    ids=[
        "nullable-with-empty-value",
        "nullable",
        "empty-value",
        "openapi-31-type-list",
        "plain",
        "two-parameters",
        "valid-on-the-wire",
        "array",
        "beside-array",
        "const",
        "untyped",
    ],
)
def test_negative_data_rejection_reports_type_mutations_of_wrapped_scalar_query(
    ctx, app_runner, version, parameters, query, reported
):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/thumb": {
                "get": {
                    "parameters": [{"in": "query", "name": name, **extra} for name, extra in parameters.items()],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )

    @app.route("/thumb")
    def thumb():
        return jsonify({})

    operation = schemathesis.openapi.from_url(app_runner.openapi_url(app))["/thumb"]["GET"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=tuple(_mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter=name) for name in query),
        ),
        query=query,
    )
    response = case.call()
    _assert_rejection(case, response, reported=reported)


_PATH_TYPE_MUTATION = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="id", location=ParameterLocation.PATH)
_HEADER_TYPE_MUTATION = _mutation(
    OperatorKind.CHANGE_TYPE, ("type",), parameter="X-Key", location=ParameterLocation.HEADER
)


def test_negative_data_rejection_ignores_type_mutations_wire_identical_in_every_location(
    response_factory, path_and_query_schema
):
    case = _path_and_query_case(
        path_and_query_schema,
        {"id": "7"},
        {"key": "7"},
        {"X-Key": "7"},
        (_PATH_TYPE_MUTATION, _QUERY_TYPE_MUTATION),
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


@pytest.mark.parametrize(
    "path_schema",
    [{"enum": ["5"]}, {"type": ["integer", "string"], "minimum": 10}, {"anyOf": [{"type": "string"}]}],
    ids=["enum-string", "type-list-minimum", "any-of-string"],
)
def test_negative_data_rejection_ignores_path_type_mutation_with_valid_wire_text(ctx, response_factory, path_schema):
    # Integer `5` reaches the server as the text `5`, which these schemas accept as a string.
    schema = ctx.openapi.load_schema(
        {
            "/items/{id}": {
                "get": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": path_schema}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    case = _path_and_query_case(schema, {"id": 5}, None, None, (_PATH_TYPE_MUTATION,))
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


@pytest.mark.parametrize(
    ("parameter", "query"),
    [("id", {"other": "5"}), ("ghost", {"ghost": "5"})],
    ids=["missing-from-query", "undeclared"],
)
def test_negative_data_rejection_reports_type_mutation_of_query_parameter(ctx, response_factory, parameter, query):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"in": "query", "name": "id", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid type string (expected integer)",
            parameter=parameter,
            parameter_location=ParameterLocation.QUERY,
        ),
        query=query,
    )
    with pytest.raises(AcceptedNegativeData, match=f"parameter `{parameter}` in query - invalid type string"):
        negative_data_rejection(check_context(), response_factory.requests(status_code=200), case)


@pytest.mark.parametrize(
    ("query", "headers", "mutations"),
    [
        ({"key": "abc"}, {"X-Key": "7"}, (_PATH_TYPE_MUTATION, _QUERY_TYPE_MUTATION)),
        ({"key": 7}, {"X-Key": "bad"}, (_PATH_TYPE_MUTATION, _HEADER_TYPE_MUTATION)),
    ],
    ids=["query-not-numeric", "header-not-numeric"],
)
def test_negative_data_rejection_reports_when_one_location_stays_invalid_on_the_wire(
    response_factory, path_and_query_schema, query, headers, mutations
):
    case = _path_and_query_case(path_and_query_schema, {"id": "7"}, query, headers, mutations)
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


@pytest.mark.parametrize(("value", "reported"), [("-1", True), ("5", False)], ids=["below-minimum", "valid"])
def test_negative_data_rejection_validates_numeric_wire_value_against_query_schema(
    response_factory, path_and_query_schema, value, reported
):
    case = _path_and_query_case(path_and_query_schema, {"id": 7}, {"key": value}, {"X-Key": 7}, (_QUERY_TYPE_MUTATION,))
    _assert_rejection(case, response_factory.requests(), reported=reported)


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ({"schema": {"type": "string"}}, -6.922717852139307e16),
        ({"schema": {"type": "string"}}, []),
        ({"schema": {"type": "integer"}, "allowEmptyValue": True}, {}),
        ({"schema": {"type": "boolean"}}, "True"),
        ({"schema": {"type": "boolean"}}, "yes"),
        ({"schema": {"type": "boolean"}}, "OFF"),
        ({"schema": {"type": "boolean"}}, 0),
    ],
    ids=[
        "number-becomes-string",
        "empty-list-is-omitted",
        "empty-object-becomes-blank",
        "capitalized-boolean",
        "yes-boolean",
        "uppercase-off-boolean",
        "integer-boolean",
    ],
)
def test_negative_data_rejection_validates_serialized_query(ctx, response_factory, parameter, value):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"in": "query", "name": "value", "required": False, **parameter}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"value": value},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    assert negative_data_rejection(check_context(), response, case) is None


@pytest.mark.parametrize("declared_type", ["integer", "number"])
@pytest.mark.parametrize("required", [True, False], ids=["required", "optional"])
def test_negative_data_rejection_blank_numeric_query(ctx, response_factory, declared_type, required):
    # Frameworks read a blank optional value as an absent one.
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"in": "query", "name": "year", "required": required, "schema": {"type": declared_type}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"year": ""},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    _assert_rejection(case, response, reported=required)


@pytest.mark.parametrize(
    ("query", "headers", "reported"),
    [
        ({"title": 5}, {"X-Mode": " 3 "}, False),
        ({"limit": "", "title": 5}, {"X-Mode": " 3 "}, False),
        ({"title": "a", "flag": "true"}, {"X-Mode": " 3 "}, False),
        ({"title": "a", "limit": 5}, {"X-Mode": " 3 "}, False),
        ({"flag": None, "title": 5}, {"X-Mode": " 3 "}, False),
        ({"title": ["a", "b"]}, {"X-Mode": " 3 "}, True),
        ({"title": 5}, {"X-Mode": "bad"}, True),
    ],
    ids=[
        "integer-sent-as-string",
        "blank-optional-before-sent",
        "boolean-spelling",
        "valid-integer",
        "unsent-before-sent",
        "repeated",
        "bad-header",
    ],
)
def test_negative_data_rejection_validates_query_and_headers_as_sent(ctx, response_factory, query, headers, reported):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"in": "query", "name": "flag", "schema": {"type": "boolean"}},
                {"in": "query", "name": "title", "required": True, "schema": {"type": "string"}},
                {"in": "query", "name": "limit", "schema": {"type": "integer"}},
                {"in": "header", "name": "X-Mode", "schema": {"type": "string", "maxLength": 2}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE, headers=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]
        ),
        query=query,
        headers=headers,
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    _assert_rejection(case, response, reported=reported)


def test_negative_data_rejection_reports_invalid_query_beside_negated_path(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"in": "query", "name": "limit", "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query={"limit": "abc"},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response, case)


@pytest.mark.parametrize(
    ("query", "transport_kwargs", "reported"),
    [
        ({}, {"json": {"name": "valid"}}, False),
        ({}, None, True),
        ({"limit": "abc"}, {"json": {"name": "valid"}}, True),
        ({"limit": 1}, None, True),
    ],
    ids=["body-replaced", "body-sent", "body-replaced-beside-invalid-query", "body-sent-beside-valid-query"],
)
def test_negative_data_rejection_skips_body_replaced_by_caller(
    ctx, response_factory, query, transport_kwargs, reported
):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "parameters": [{"in": "query", "name": "limit", "schema": {"type": "integer"}}],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE if query else None,
            body=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query=query,
        body={"name": 5},
        media_type="application/json",
    )
    context = check_context(transport_kwargs=transport_kwargs)
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    _assert_rejection(case, response, reported=reported, context=context)


def test_negative_data_rejection_validates_repeated_query_with_allowed_empty_value(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "page",
                    "schema": {"anyOf": [{"type": "integer"}, {"type": "string", "enum": ["first"]}]},
                    "allowEmptyValue": True,
                },
                {"in": "query", "name": "filter_dead", "schema": {"type": "boolean"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=(_mutation(OperatorKind.NEGATE_CONSTRAINTS, ("anyOf",), parameter="page"),),
        ),
        query={"page": ["not-an-integer", ""], "filter_dead": True},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    assert negative_data_rejection(check_context(), response, case) is None


@pytest.mark.parametrize(
    ("parameter", "value", "query", "reported"),
    [
        ({"schema": {"type": "string"}}, {"first": [], "second": None}, "title=first&title=second", False),
        ({"schema": {"type": "string", "maxLength": 3}}, {"long": [], "ok": None}, "title=long&title=ok", False),
        ({"schema": {"enum": ["ok"]}}, {"bad": [], "worse": None}, "title=bad&title=worse", True),
        ({"schema": {"enum": ["ok"]}}, {"bad": [], "ok": None}, "title=bad&title=ok", False),
        (
            {"schema": {"enum": ["asc"]}, "allowEmptyValue": True},
            ["null", {"key": [], "": None}],
            "title=null&title=key&title=",
            False,
        ),
        ({"schema": {"enum": ["asc"]}}, ["null", {"key": [], "": None}], "title=null&title=key&title=", True),
        ({"schema": {"type": "string"}}, [[{"key": "value"}]], "title=%7B%27key%27%3A+%27value%27%7D", False),
        ({"schema": {"enum": ["asc"]}}, [[{"key": "value"}]], "title=%7B%27key%27%3A+%27value%27%7D", True),
        (
            {"schema": {"type": "string", "maxLength": 200}, "allowEmptyValue": True},
            [[{"key": "value"}]],
            "title=%7B%27key%27%3A+%27value%27%7D",
            False,
        ),
        (
            {"schema": {"type": "array", "items": {"enum": ["ok"]}}},
            {"bad": [], "ok": None},
            "title=bad&title=ok",
            True,
        ),
        *(
            ({"schema": {"type": "number", "maximum": 10}}, ["bad", {text: None}], f"title=bad&title={text}", False)
            for text in ("nan", "inf", "-Infinity", "1e400")
        ),
        ({"schema": {"type": "boolean"}}, ["abc", "yes"], "title=abc&title=yes", False),
        ({"schema": {"type": "boolean"}}, ["abc", {"On": None}], "title=abc&title=On", False),
        ({"schema": {"type": "boolean"}}, ["abc", {"enabled": None}], "title=abc&title=enabled", True),
    ],
    ids=[
        "object-keys",
        "one-key-valid",
        "all-keys-invalid",
        "enum-key-valid",
        "nested-object-sends-allowed-empty-value",
        "nested-object-all-invalid",
        "nested-list-sends-object-text",
        "nested-list-object-text-invalid",
        "nested-list-object-text-with-allowed-empty-value",
        "declared-array",
        "nested-nan",
        "nested-inf",
        "nested-negative-infinity",
        "nested-overflow",
        "boolean-spelling-element",
        "nested-boolean-spelling",
        "nested-unknown-boolean-spelling",
    ],
)
def test_negative_data_rejection_validates_scalar_query_by_sent_values(
    ctx, response_factory, parameter, value, query, reported
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"in": "query", "name": "title", **parameter}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=(_mutation(OperatorKind.NEGATE_CONSTRAINTS, ("enum",), parameter="title"),),
        ),
        query={"title": value},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    assert response.request.url == f"http://127.0.0.1/items?{query}"
    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize(
    ("license_required", "license", "collection", "path", "url", "reported"),
    [
        (False, [], "", "/audio", "http://127.0.0.1/audio?collection=", False),
        (False, None, "", "/audio", "http://127.0.0.1/audio?collection=", False),
        (True, [], "", "/audio", "http://127.0.0.1/audio?collection=", True),
        (False, [], "bogus", "/audio", "http://127.0.0.1/audio?collection=bogus", True),
        (False, [], "", "/other", "http://127.0.0.1/other?collection=", True),
        (False, [], ["bogus", ""], "/audio", "http://127.0.0.1/audio?collection=bogus&collection=", False),
        (False, [], {"tag": None}, "/audio", "http://127.0.0.1/audio?collection=tag", False),
        (False, None, ["bogus", "worse"], "/audio", "http://127.0.0.1/audio?collection=bogus&collection=worse", True),
    ],
    ids=[
        "empty-list",
        "none",
        "required",
        "invalid-sibling",
        "other-path",
        "repeated-sibling",
        "object-sibling",
        "repeated-invalid-sibling",
    ],
)
def test_negative_data_rejection_validates_optional_query_omitted_on_the_wire(
    ctx, response_factory, license_required, license, collection, path, url, reported
):
    operation = ctx.openapi.load_operation(
        "GET /audio",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "license",
                    "required": license_required,
                    "allowEmptyValue": True,
                    "schema": {"type": "string"},
                },
                {
                    "in": "query",
                    "name": "collection",
                    "allowEmptyValue": True,
                    "schema": {"type": "string", "enum": ["tag", "source", "creator"]},
                },
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=(
                _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("enum",), parameter="license"),
                _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("enum",), parameter="collection"),
            ),
        ),
        query={"license": license, "collection": collection},
    )
    response = response_factory.requests()
    response.request.prepare_url(f"http://127.0.0.1{path}", case.as_transport_kwargs()["params"])

    assert response.request.url == url
    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize(
    ("authority", "reported"),
    [("0", False), (0, False), ("yes", False), ("enabled", True)],
    ids=["zero-text", "zero-integer", "yes", "unknown-spelling"],
)
def test_negative_data_rejection_validates_boolean_spelling_beside_another_mutation(
    ctx, response_factory, authority, reported
):
    operation = ctx.openapi.load_operation(
        "GET /audio",
        {
            "parameters": [
                {"in": "query", "name": "title", "schema": {"type": "string", "minLength": 1}},
                {"in": "query", "name": "authority", "schema": {"type": "boolean", "default": False}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=(
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="title"),
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="authority"),
            ),
        ),
        query={"title": 0.0, "authority": authority},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/audio", case.as_transport_kwargs()["params"])

    _assert_rejection(case, response, reported=reported)


_NULLABLE_FLAG = {"schema": {"type": "boolean", "nullable": True}, "allowEmptyValue": True}
_NEGATED_NULLABLE_FLAG = _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("anyOf",), parameter="compressed")


@pytest.mark.parametrize(
    ("version", "parameters", "query", "mutations", "path", "url", "reported"),
    [
        (
            "3.0.2",
            [
                {
                    "in": "query",
                    "name": "full_size",
                    "schema": {"type": "boolean", "nullable": True, "default": False},
                    "allowEmptyValue": True,
                },
                {"in": "query", "name": "compressed", **_NULLABLE_FLAG},
            ],
            {"compressed": "null", "full_size": "null"},
            (
                _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("anyOf",), parameter="full_size"),
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="compressed"),
            ),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=null&full_size=null",
            False,
        ),
        (
            "3.1.0",
            [{"in": "query", "name": "compressed", "schema": {"type": ["boolean", "null"]}}],
            {"compressed": "null"},
            (_mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="compressed"),),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=null",
            False,
        ),
        (
            "2.0",
            [{"in": "query", "name": "compressed", "type": "boolean", "x-nullable": True}],
            {"compressed": "null"},
            (_NEGATED_NULLABLE_FLAG,),
            "/api/thumb",
            "http://127.0.0.1/api/thumb?compressed=null",
            False,
        ),
        (
            "3.0.2",
            [{"in": "query", "name": "compressed", **_NULLABLE_FLAG}],
            {"compressed": "NULL"},
            (_NEGATED_NULLABLE_FLAG,),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=NULL",
            False,
        ),
        (
            "3.0.2",
            [{"in": "query", "name": "compressed", **_NULLABLE_FLAG}],
            {"compressed": "Null"},
            (_NEGATED_NULLABLE_FLAG,),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=Null",
            False,
        ),
        (
            "3.0.2",
            [{"in": "query", "name": "compressed", **_NULLABLE_FLAG}],
            {"compressed": ["bogus", "null"]},
            (_NEGATED_NULLABLE_FLAG,),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=bogus&compressed=null",
            False,
        ),
        (
            "3.0.2",
            [{"in": "query", "name": "compressed", "schema": {"type": "boolean"}}],
            {"compressed": "null"},
            (_mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="compressed"),),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=null",
            True,
        ),
        (
            "3.0.2",
            [
                {"in": "query", "name": "compressed", **_NULLABLE_FLAG},
                {"in": "query", "name": "collection", "schema": {"type": "string", "enum": ["tag"]}},
            ],
            {"compressed": "null", "collection": "bogus"},
            (_NEGATED_NULLABLE_FLAG, _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("enum",), parameter="collection")),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=null&collection=bogus",
            True,
        ),
        (
            "3.0.2",
            [{"in": "query", "name": "compressed", **_NULLABLE_FLAG}],
            {"compressed": "null"},
            (_NEGATED_NULLABLE_FLAG,),
            "/other",
            "http://127.0.0.1/other?compressed=null",
            True,
        ),
        (
            "3.0.2",
            [
                {"in": "query", "name": "full_size", **_NULLABLE_FLAG},
                {"in": "query", "name": "compressed", **_NULLABLE_FLAG},
            ],
            {"full_size": "true", "compressed": "null", "x": []},
            (_ADDITIONAL_PROPERTIES_MUTATION,),
            "/thumb",
            "http://127.0.0.1/thumb?full_size=true&compressed=null",
            False,
        ),
        (
            "3.0.2",
            [
                {"in": "query", "name": "full_size", **_NULLABLE_FLAG},
                {"in": "query", "name": "compressed", **_NULLABLE_FLAG},
            ],
            {"compressed": "null", "": "false"},
            (_ADDITIONAL_PROPERTIES_MUTATION,),
            "/thumb",
            "http://127.0.0.1/thumb?compressed=null&=false",
            False,
        ),
    ],
    ids=[
        "openapi-30-nullable",
        "openapi-31-type-list",
        "swagger-20-x-nullable",
        "uppercase",
        "capitalized",
        "repeated",
        "not-nullable",
        "another-invalid-parameter",
        "other-path",
        "unsent-extra-parameter",
        "sent-extra-parameter",
    ],
)
@pytest.mark.parametrize("phase", [TestPhase.FUZZING, TestPhase.STATEFUL], ids=["fuzzing", "stateful"])
def test_negative_data_rejection_validates_null_text_for_nullable_query(
    ctx, response_factory, version, parameters, query, mutations, path, url, reported, phase
):
    operation = ctx.openapi.load_operation(
        "GET /thumb", {"parameters": parameters, "responses": {"200": {"description": "OK"}}}, version=version
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=mutations,
            phase=phase,
        ),
        query=query,
    )
    response = response_factory.requests()
    response.request.prepare_url(f"http://127.0.0.1{path}", case.as_transport_kwargs()["params"])

    assert response.request.url == url
    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize("location", [ParameterLocation.HEADER, ParameterLocation.COOKIE], ids=["header", "cookie"])
@pytest.mark.parametrize(
    ("value", "reported"),
    [("yes", False), ("0", False), ("True", False), ("enabled", True)],
    ids=["yes", "zero", "capitalized", "unknown-spelling"],
)
def test_negative_data_rejection_validates_boolean_spelling_in_headers_and_cookies(
    ctx, response_factory, location, value, reported
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"in": location.value, "name": "flag", "required": True, "schema": {"type": "boolean"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = _negative_case(operation, location, "flag", None, **{location.container_name: {"flag": value}})
    _assert_rejection(case, response_factory.requests(), reported=reported)


@pytest.mark.parametrize(
    ("value", "reported"),
    [
        ("a" * 255 + " ", False),
        ("a" * 252 + "\t\t\t\t", False),
        (" \t", True),
        ("a" * 256 + " ", True),
    ],
    ids=["trailing-space", "trailing-tabs", "only-whitespace", "too-long"],
)
@pytest.mark.parametrize("extra", [{}, {"X-Extra": "1"}], ids=["declared-only", "with-undeclared-header"])
def test_negative_data_rejection_validates_headers_without_surrounding_whitespace(
    ctx, response_factory, value, reported, extra
):
    # HTTP servers drop leading and trailing spaces and tabs from header values before the application reads them.
    operation = ctx.openapi.load_operation(
        "POST /tasks",
        {
            "parameters": [
                {
                    "in": "header",
                    "name": "Idempotency-Key",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 255},
                }
            ],
            "responses": {"201": {"description": "Created"}},
        },
    )
    case = _negative_case(
        operation, ParameterLocation.HEADER, "Idempotency-Key", None, headers={"Idempotency-Key": value, **extra}
    )
    response = response_factory.requests(status_code=201)
    _assert_rejection(case, response, reported=reported)


def test_negative_data_rejection_reports_object_query_with_another_invalid_parameter(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"in": "query", "name": "title", "schema": {"type": "string"}},
                {"in": "header", "name": "X-Mode", "schema": {"enum": ["fast"]}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=(
                _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("type",), parameter="title"),
                _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("enum",), "X-Mode", ParameterLocation.HEADER),
            ),
        ),
        query={"title": {"first": [], "second": None}},
        headers={"X-Mode": "slow"},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response, case)


@pytest.mark.parametrize(
    ("extra_schema", "extra_value", "url"),
    [
        ({"type": "integer"}, 5, "http://127.0.0.1/items"),
        ({"type": "number"}, 1.5, "http://127.0.0.1/items"),
        ({"type": "boolean"}, True, "http://127.0.0.1/items"),
        ({"type": "array", "items": {"type": "integer"}}, [1], "http://127.0.0.1/items"),
        ({"type": "array", "items": {"type": "integer"}}, [1, 2], "http://127.0.0.1/items"),
        ({"enum": ["123"]}, "123", "http://127.0.0.1/items"),
        ({"type": "integer"}, 5, "http://127.0.0.1/items?api_key=secret"),
    ],
    ids=[
        "integer",
        "number",
        "boolean",
        "single-item-array",
        "array",
        "untyped-enum",
        "query-key-added-outside-case",
    ],
)
def test_negative_data_rejection_validates_serialized_query_with_typed_values(
    ctx, response_factory, extra_schema, extra_value, url
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"in": "query", "name": "value", "schema": {"type": "string"}},
                {"in": "query", "name": "extra", "schema": extra_schema},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"value": 42, "extra": extra_value},
    )
    response = response_factory.requests()
    response.request.prepare_url(url, case.as_transport_kwargs()["params"])

    assert negative_data_rejection(check_context(), response, case) is None


@pytest.mark.parametrize(
    ("parameter_schema", "required", "value"),
    [
        ({"type": "integer"}, False, "not-an-integer"),
        ({"type": "integer"}, True, []),
        ({"type": "integer"}, False, "1_0"),
        ({"type": "integer"}, False, " 5"),
        ({"type": "integer"}, False, "\u0665"),
        ({"type": "boolean"}, False, "enabled"),
    ],
    ids=["text", "omitted-required", "underscore", "whitespace", "non-ascii-digit", "unknown-boolean-spelling"],
)
def test_negative_data_rejection_reports_invalid_serialized_query(
    ctx, response_factory, parameter_schema, required, value
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "value",
                    "required": required,
                    "schema": parameter_schema,
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"value": value},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response, case)


_UNPARSABLE_INTEGER_TEXTS = pytest.mark.parametrize(
    "value", ["1_0", " 5", "\u0665"], ids=["underscore", "whitespace", "non-ascii"]
)


@_UNPARSABLE_INTEGER_TEXTS
def test_negative_data_rejection_reports_unparsable_integer_in_query_array(ctx, response_factory, value):
    operation = ctx.openapi.load_operation(
        "GET /items", {"parameters": [{"in": "query", "name": "value", "schema": {"type": "integer"}}]}
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"value": [value]},
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


@_UNPARSABLE_INTEGER_TEXTS
@pytest.mark.parametrize("location", [ParameterLocation.PATH, ParameterLocation.QUERY], ids=["path", "query"])
def test_negative_data_rejection_reports_unparsable_integer_type_mutation(ctx, response_factory, value, location):
    operation = ctx.openapi.load_operation(
        "GET /items/{value}",
        {
            "parameters": [
                {"in": "path", "name": "value", "required": True, "schema": {"type": "integer"}},
                {"in": "query", "name": "value", "required": True, "schema": {"type": "integer"}},
            ]
        },
    )
    mutation = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="value", location=location)
    meta = build_metadata(generation_modes=[GenerationMode.NEGATIVE], mutations=(mutation,))
    meta.components = {location: ComponentInfo(mode=GenerationMode.NEGATIVE)}
    case = operation.Case(
        _meta=meta,
        path_parameters={"value": value if location == ParameterLocation.PATH else 1},
        query={"value": value if location == ParameterLocation.QUERY else 1},
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


@_UNPARSABLE_INTEGER_TEXTS
def test_negative_data_rejection_reports_unparsable_integer_in_path_array(ctx, response_factory, value):
    operation = ctx.openapi.load_operation(
        "GET /items/{values}",
        {
            "parameters": [
                {
                    "in": "path",
                    "name": "values",
                    "required": True,
                    "schema": {"type": "array", "items": {"type": "integer"}},
                }
            ]
        },
    )
    case = operation.Case(
        _meta=build_metadata(path_parameters=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        path_parameters={"values": f"{value},2"},
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


# Servers commonly parse these as floats, so they may legitimately read them as numbers.
@pytest.mark.parametrize("value", ["nan", "inf", "-Infinity", "1e400"])
@pytest.mark.parametrize("location", [ParameterLocation.PATH, ParameterLocation.QUERY], ids=["path", "query"])
def test_negative_data_rejection_ignores_non_finite_numeric_wire_value(ctx, response_factory, value, location):
    operation = ctx.openapi.load_operation(
        "GET /rate/{value}",
        {
            "parameters": [
                {"in": "path", "name": "value", "required": True, "schema": {"type": "number"}},
                {"in": "query", "name": "value", "required": True, "schema": {"type": "number"}},
            ]
        },
    )
    mutation = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="value", location=location)
    meta = build_metadata(generation_modes=[GenerationMode.NEGATIVE], mutations=(mutation,))
    meta.components = {location: ComponentInfo(mode=GenerationMode.NEGATIVE)}
    case = operation.Case(
        _meta=meta,
        path_parameters={"value": value if location == ParameterLocation.PATH else 1.5},
        query={"value": value if location == ParameterLocation.QUERY else 1.5},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_ignores_huge_integer_wire_value(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items/{id}",
        {"parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}]},
    )
    mutation = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="id", location=ParameterLocation.PATH)
    meta = build_metadata(generation_modes=[GenerationMode.NEGATIVE], mutations=(mutation,))
    meta.components = {ParameterLocation.PATH: ComponentInfo(mode=GenerationMode.NEGATIVE)}
    case = operation.Case(_meta=meta, path_parameters={"id": "9" * 400})
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_reports_non_string_path_value(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items/{id}",
        {"parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "string", "minLength": 3}}]},
    )
    case = operation.Case(
        _meta=build_metadata(path_parameters=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        path_parameters={"id": 5},
    )
    with pytest.raises(AcceptedNegativeData, match="Invalid data should have been rejected"):
        negative_data_rejection(check_context(), response_factory.requests(), case)


def test_negative_data_rejection_ignores_non_finite_element_in_query_array(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /rates", {"parameters": [{"in": "query", "name": "rate", "schema": {"type": "number"}}]}
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter="rate",
            parameter_location=ParameterLocation.QUERY,
        ),
        query={"rate": [{"a": None}, "nan"]},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_ignores_non_finite_item_in_path_array(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /rates/{values}",
        {
            "parameters": [
                {
                    "in": "path",
                    "name": "values",
                    "required": True,
                    "schema": {"type": "array", "items": {"type": "number"}},
                }
            ]
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter="values",
            parameter_location=ParameterLocation.PATH,
            mutations=(
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="values", location=ParameterLocation.PATH),
            ),
        ),
        path_parameters={"values": EncodedPath("1.5,inf")},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


_READ_ONLY_COMPONENTS = {
    "schemas": {
        "Item": {
            "type": "object",
            "properties": {"id": {"type": "integer", "readOnly": True}, "name": {"type": "string"}},
            "required": ["id", "name"],
        },
        "Fields": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "Items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
        "Widget": {
            "allOf": [
                {"$ref": "#/components/schemas/Fields"},
                {"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}}},
            ],
            "required": ["id", "name"],
        },
        "Legacy": {
            "type": "object",
            "properties": {"legacy": {"not": {}}, "name": {"type": "string"}},
            "required": ["name"],
        },
        "Unreadable": {
            "type": "object",
            "properties": {"id": {"type": "integer", "readOnly": True}, "legacy": False},
        },
        "Nullable": {
            "type": "object",
            "nullable": True,
            "properties": {"id": {"type": "integer", "readOnly": True}, "name": {"type": "string"}},
        },
        "Choice": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"id": {"type": "integer", "readOnly": True}, "a": {"type": "string"}},
                    "required": ["a"],
                },
                {
                    "type": "object",
                    "properties": {"id": {"type": "integer", "readOnly": True}, "b": {"type": "string"}},
                    "required": ["b"],
                },
            ]
        },
    }
}


# A server may ignore a read-only property instead of rejecting it, so that alone is not a failure.
@pytest.mark.parametrize(
    ("ref", "body", "reported"),
    [
        ("Item", {"name": "", "id": {}}, False),
        ("Widget", {"name": "", "id": {}}, False),
        ("Item", {"name": 1, "id": {}}, True),
        ("Item", {"id": {}}, True),
        ("Items", [{"name": "", "id": {}}], False),
        ("Items", [{"name": 1, "id": {}}], True),
        ("Choice", {"a": "x", "id": 1}, False),
        ("Choice", {"a": 1, "id": 1}, True),
        ("Legacy", {"name": "x", "legacy": 1}, False),
        ("Nullable", {"name": "", "id": {}}, False),
        ("Nullable", {"name": 1, "id": {}}, True),
        ("Item", {"name": "x"}, True),
        ("Unreadable", {"id": 1}, True),
    ],
    ids=[
        "flat",
        "allOf",
        "other-violation",
        "missing-required",
        "array",
        "array-other-violation",
        "one-of",
        "one-of-other-violation",
        "forbidden-without-read-only",
        "nullable",
        "nullable-other-violation",
        "body-without-read-only-property",
        "schema-the-validator-rejects",
    ],
)
def test_negative_data_rejection_read_only_property(ctx, response_factory, ref, body, reported):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}},
            },
            "responses": {"201": {"description": "Created"}, "400": {"description": "Bad Request"}},
        },
        components=_READ_ONLY_COMPONENTS,
    )
    case = operation.Case(
        body=body,
        media_type="application/json",
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
    )
    response = response_factory.requests(status_code=201)
    _assert_rejection(case, response, reported=reported)


# Only the alternative matching the case media type decides; an undeclared one leaves the failure standing.
@pytest.mark.parametrize(
    ("declared", "media_type", "reported"),
    [
        ("application/xml", "application/xml", False),
        ("application/xml; charset=utf-8", "application/xml", False),
        ("application/xml", "application/yaml", True),
    ],
    ids=["declared-media-type", "declared-with-parameters", "undeclared-media-type"],
)
def test_negative_data_rejection_read_only_property_media_types(ctx, response_factory, declared, media_type, reported):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
                    declared: {"schema": {"$ref": "#/components/schemas/Item"}},
                },
            },
            "responses": {"201": {"description": "Created"}, "400": {"description": "Bad Request"}},
        },
        components=_READ_ONLY_COMPONENTS,
    )
    case = operation.Case(
        body={"name": "", "id": {}},
        media_type=media_type,
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
    )
    response = response_factory.requests(status_code=201)
    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize(
    ("media_type", "body_mode", "query_mode", "header_mode", "expected"),
    [
        ("text/plain", GenerationMode.NEGATIVE, None, None, True),
        ("application/octet-stream", GenerationMode.NEGATIVE, None, None, True),
        ("application/json", GenerationMode.NEGATIVE, None, None, False),
        ("text/plain", GenerationMode.NEGATIVE, GenerationMode.NEGATIVE, None, False),
        ("text/plain", GenerationMode.NEGATIVE, None, GenerationMode.NEGATIVE, False),
        ("text/plain", GenerationMode.NEGATIVE, GenerationMode.NEGATIVE, GenerationMode.NEGATIVE, False),
        ("text/plain", None, GenerationMode.NEGATIVE, None, False),
    ],
)
def test_body_negation_becomes_valid_after_serialization(
    ctx, response_factory, media_type, body_mode, query_mode, header_mode, expected
):
    operation = ctx.openapi.load_operation(
        "PUT /endpoint",
        {
            "parameters": [
                {"in": "query", "name": "key", "schema": {"type": "integer"}},
                {"in": "header", "name": "X-Key", "schema": {"type": "integer"}},
            ],
            "requestBody": {
                "required": True,
                "content": {media_type: {"schema": {"type": "string"}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            body=body_mode,
            query=query_mode,
            headers=header_mode,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query={"key": "bad"} if query_mode is not None else {},
        headers={"X-Key": "bad"} if header_mode is not None else {},
        body={},
        media_type=media_type,
    )
    assert _body_negation_becomes_valid_after_serialization(response_factory.requests(), case) is expected


@pytest.mark.parametrize(
    ("query", "headers", "expected"),
    [
        ({"flag": "true"}, None, True),
        ({"flag": "true"}, {"X-Mode": "bad"}, False),
        ({"flag": "maybe"}, None, False),
    ],
    ids=["boolean-spelling", "invalid-header-after-valid-query", "invalid-boolean"],
)
def test_body_negation_becomes_valid_after_serialization_reads_other_locations_as_sent(
    ctx, response_factory, query, headers, expected
):
    operation = ctx.openapi.load_operation(
        "PUT /endpoint",
        {
            "parameters": [
                {"in": "query", "name": "flag", "schema": {"type": "boolean"}},
                {"in": "header", "name": "X-Mode", "schema": {"type": "string", "maxLength": 2}},
            ],
            "requestBody": {"required": True, "content": {"text/plain": {"schema": {"type": "string"}}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            body=GenerationMode.NEGATIVE,
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE if headers else None,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query=query,
        headers=headers or {},
        body=5,
        media_type="text/plain",
    )
    assert _body_negation_becomes_valid_after_serialization(response_factory.requests(), case) is expected


MULTIPART = "multipart/form-data"
URLENCODED = "application/x-www-form-urlencoded"


def _load_form_operation(ctx, app_runner, media_type, properties=None, encoding=None, version="3.0.2"):
    definition = {
        "schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": ["dmca", "mature", "other"]},
                "description": {"type": "string", "nullable": True},
                **(properties or {}),
            },
            "required": ["reason"],
        }
    }
    if encoding is not None:
        definition["encoding"] = encoding
    app, _ = ctx.openapi.make_flask_app(
        {
            "/post": {
                "post": {
                    "parameters": [{"in": "query", "name": "limit", "schema": {"type": "integer"}}],
                    "requestBody": {"required": True, "content": {media_type: definition}},
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
        version=version,
    )

    @app.route("/post", methods=["POST"])
    def post():
        return jsonify({}), 201

    return schemathesis.openapi.from_url(app_runner.openapi_url(app))["/post"]["POST"]


@pytest.mark.parametrize(
    ("media_type", "properties", "value", "wire"),
    [
        (MULTIPART, None, True, b'name="description"\r\n\r\ntrue\r\n'),
        (URLENCODED, None, True, "description=true"),
        (MULTIPART, {"count": {"type": "string", "pattern": "^[0-9]+$"}}, 42, b'name="count"\r\n\r\n42\r\n'),
        (MULTIPART, None, 1.5, b'name="description"\r\n\r\n1.5\r\n'),
        (URLENCODED, None, 1.5, "description=1.5"),
        (MULTIPART, None, [True, 1], b'name="description"\r\n\r\n1\r\n'),
        (MULTIPART, None, ["x", {"b": None}], b'name="description"\r\n\r\n{"b": null}\r\n'),
        (MULTIPART, {"count": {"type": "integer", "maximum": 10}}, 5, b'name="count"\r\n\r\n5\r\n'),
        (URLENCODED, None, {"x": 1}, "description=x"),
    ],
    ids=[
        "multipart-boolean",
        "urlencoded-boolean",
        "multipart-integer",
        "multipart-float",
        "urlencoded-float",
        "repeated-values",
        "repeated-values-with-object",
        "integer-read-from-text",
        "object-sent-as-its-keys",
    ],
)
def test_negative_data_rejection_accepts_form_scalars_sent_as_valid_strings(
    ctx, app_runner, media_type, properties, value, wire
):
    operation = _load_form_operation(ctx, app_runner, media_type, properties)
    name = "count" if properties else "description"
    case = operation.Case(
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        body={"reason": "dmca", name: value},
        media_type=media_type,
    )
    response = case.call()
    assert wire in response.request.body
    assert negative_data_rejection(check_context(), response, case) is None


def test_negative_data_rejection_accepts_form_scalars_declared_with_media_type_parameters(ctx, app_runner):
    # Generated cases carry the media type without the declared `; charset=utf-8`.
    operation = _load_form_operation(ctx, app_runner, f"{URLENCODED}; charset=utf-8")
    case = operation.Case(
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        body={"reason": "dmca", "description": True},
        media_type=URLENCODED,
    )
    response = case.call()
    assert "description=true" in response.request.body
    assert negative_data_rejection(check_context(), response, case) is None


@pytest.mark.parametrize(
    ("media_type", "value"),
    [(MULTIPART, []), (URLENCODED, []), (URLENCODED, {})],
    ids=["multipart-empty-list", "urlencoded-empty-list", "urlencoded-empty-object"],
)
def test_negative_data_rejection_accepts_optional_form_fields_not_sent(ctx, app_runner, media_type, value):
    operation = _load_form_operation(ctx, app_runner, media_type)
    case = operation.Case(
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        body={"reason": "dmca", "description": value},
        media_type=media_type,
    )
    response = case.call()
    assert "description" not in (
        response.request.body.decode() if isinstance(response.request.body, bytes) else response.request.body
    )
    assert negative_data_rejection(check_context(), response, case) is None


@pytest.mark.parametrize(
    ("media_type", "properties", "encoding", "version", "body", "query"),
    [
        (MULTIPART, {"description": {"type": "string", "maxLength": 3}}, None, "3.0.2", {"description": True}, None),
        (MULTIPART, None, None, "3.0.2", {"reason": True}, None),
        (URLENCODED, None, None, "3.0.2", {"reason": True}, None),
        (MULTIPART, None, None, "3.0.2", {"reason": True, "description": True}, None),
        (MULTIPART, None, None, "3.0.2", {"description": True}, {"limit": "bad"}),
        ("application/json", None, None, "3.0.2", {"description": True}, None),
        (MULTIPART, None, {"description": {"contentType": "application/json"}}, "3.0.2", {"description": True}, None),
        (MULTIPART, {"meta": {"type": ["object", "string"]}}, None, "3.1.0", {"meta": True}, None),
        (
            MULTIPART,
            {"tags": {"type": ["array", "string"], "items": {"type": "object"}}},
            None,
            "3.1.0",
            {"tags": True},
            None,
        ),
        (MULTIPART, {"file": {"type": "string", "format": "binary"}}, None, "3.0.2", {"file": True}, None),
        (MULTIPART, None, None, "3.0.2", {"reason": ["bad", "worse"]}, None),
        (MULTIPART, None, None, "3.0.2", {"reason": []}, None),
        (URLENCODED, None, None, "3.0.2", {"reason": {}}, None),
        (
            MULTIPART,
            {"file": {"type": "string", "format": "binary"}},
            None,
            "3.0.2",
            {"file": b"\x00", "description": True},
            None,
        ),
        (
            MULTIPART,
            {'na"me': {"type": "string"}},
            {'na"me': {"contentType": "application/json"}},
            "3.0.2",
            {'na"me': True},
            None,
        ),
        (URLENCODED, {"count": {"type": "string", "minLength": 1}}, None, "3.0.2", {"count": ""}, None),
        (
            MULTIPART,
            {"description": {"type": "string", "maxLength": 3, "nullable": True}},
            None,
            "3.0.2",
            {"description": True},
            None,
        ),
    ],
    ids=[
        "max-length",
        "enum",
        "urlencoded-enum",
        "one-of-two-fields-still-invalid",
        "invalid-query",
        "json",
        "json-encoded-part",
        "object-field",
        "array-field",
        "binary-field",
        "repeated-field-all-invalid",
        "required-field-omitted",
        "urlencoded-required-field-omitted",
        "binary-content-cannot-be-validated",
        "escaped-name-typed-part",
        "urlencoded-blank-field",
        "nullable-max-length",
    ],
)
def test_negative_data_rejection_reports_form_scalars_invalid_when_sent(
    ctx, app_runner, media_type, properties, encoding, version, body, query
):
    operation = _load_form_operation(ctx, app_runner, media_type, properties, encoding, version)
    case = operation.Case(
        _meta=build_metadata(
            body=GenerationMode.NEGATIVE,
            query=GenerationMode.NEGATIVE if query else None,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query=query or {},
        body={"reason": "dmca", **body},
        media_type=media_type,
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), case.call(), case)


def test_negative_data_rejection_reports_form_body_with_undeclared_media_type(ctx, app_runner):
    operation = _load_form_operation(ctx, app_runner, URLENCODED)
    case = operation.Case(
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        body={"reason": "dmca", "description": True},
        media_type=MULTIPART,
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), case.call(), case)


def test_response_schema_conformance_with_unspecified_method(response_factory, sample_raw_schema):
    response = response_factory.requests()
    response = Response.from_requests(response, True)
    sample_raw_schema["paths"]["/test"]["post"]["responses"] = {
        "200": {
            "description": "Successful response",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                        "required": ["id", "name"],
                    }
                }
            },
        }
    }
    schema = schemathesis.openapi.from_dict(sample_raw_schema)
    operation = schema["/test"]["POST"]
    case = operation.Case(
        _meta=CaseMetadata(
            generation=GenerationInfo(
                time=0.1,
                mode=GenerationMode.NEGATIVE,
            ),
            components={
                ParameterLocation.QUERY: ComponentInfo(mode=GenerationMode.NEGATIVE),
            },
            phase=PhaseInfo.coverage(
                CoverageScenario.UNSPECIFIED_HTTP_METHOD,
                description="Unspecified HTTP method: PUT",
            ),
        ),
        query={"key": 5, "unknown": 3},
    )

    result = response_schema_conformance(
        check_context(),
        response,
        case,
    )
    assert result is True


@pytest.mark.parametrize(
    ("status_code", "expected_statuses", "is_positive", "should_raise"),
    [
        (200, ["200", "400"], True, False),
        (400, ["200", "400"], True, False),
        (300, ["200", "400"], True, True),
        (200, ["2XX", "4XX"], True, False),
        (299, ["2XX", "4XX"], True, False),
        (400, ["2XX", "4XX"], True, False),
        (500, ["2XX", "4XX"], True, True),
        (200, ["200", "201", "400", "401"], True, False),
        (201, ["200", "201", "400", "401"], True, False),
        (400, ["200", "201", "400", "401"], True, False),
        (402, ["200", "201", "400", "401"], True, True),
        (200, ["2XX", "3XX", "4XX"], True, False),
        (300, ["2XX", "3XX", "4XX"], True, False),
        (400, ["2XX", "3XX", "4XX"], True, False),
        (500, ["2XX", "3XX", "4XX"], True, True),
        # Negative data, should not raise
        (200, ["200", "400"], False, False),
        (400, ["200", "400"], False, False),
    ],
)
def test_positive_data_acceptance(
    response_factory,
    sample_schema,
    status_code,
    expected_statuses,
    is_positive,
    should_raise,
):
    operation = sample_schema["/test"]["POST"]
    response = response_factory.requests(status_code=status_code)
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.POSITIVE if is_positive else GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.POSITIVE if is_positive else GenerationMode.NEGATIVE],
        ),
    )
    ctx = check_context(ChecksConfig.from_dict({"positive_data_acceptance": {"expected-statuses": expected_statuses}}))

    if should_raise:
        with pytest.raises(Failure) as exc_info:
            positive_data_acceptance(ctx, response, case)
        assert "Schema-valid request rejected" in exc_info.value.title
    else:
        assert positive_data_acceptance(ctx, response, case) is None


def test_positive_data_acceptance_passes_for_rate_limiting(response_factory, sample_schema):
    # A throttle refuses the request without judging its data.
    case = sample_schema["/test"]["POST"].Case(_meta=build_metadata())
    assert positive_data_acceptance(check_context(), response_factory.requests(status_code=429), case) is None


@pytest.mark.parametrize(
    ["path", "header_name", "expected_status"],
    [
        ("/success", "X-API-Key-1", "200"),  # Does not fail
        ("/success", "X-API-Key-1", "406"),  # Fails because the response is HTTP 200
        ("/basic", "Authorization", "406"),  # Does not fail because Authorization has its own check
        ("/success", "Authorization", "200"),  # Fails because response is not 401
    ],
)
def test_missing_required_header(ctx, cli, snapshot_cli, path, header_name, expected_status):
    api = ctx.openapi.apps.success_and_basic()
    schema_path = ctx.openapi.write_schema(
        {
            path: {
                "get": {
                    "parameters": [
                        {"name": header_name, "in": "header", "required": True, "schema": {"type": "string"}},
                        {"name": "X-API-Key-2", "in": "header", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={api.base_url}/api",
            "--phases=coverage",
            "--mode=negative",
            "--checks=missing_required_header",
            config={"checks": {"missing_required_header": {"expected-statuses": [expected_status]}}},
        )
        == snapshot_cli
    )


def verify_missing_required_header(cassette_path, header, expected_status):
    with cassette_path.open(encoding="utf-8") as fd:
        cassette = yaml.safe_load(fd)
    interactions = cassette["http_interactions"]

    missing_header_interaction = next(
        (
            interaction
            for interaction in interactions
            if (
                interaction["phase"]["name"] == "coverage"
                and interaction["generation"]["mode"] == "negative"
                and interaction["phase"]["data"]["description"] == f"Missing `{header}` at header"
            )
        ),
        None,
    )

    assert missing_header_interaction is not None, f"Should find missing required header: {header}"
    phase_data = missing_header_interaction["phase"]["data"]
    assert phase_data["parameter"] == header
    assert phase_data["parameter_location"] == "header"

    request_headers = missing_header_interaction["request"]["headers"]
    assert header not in request_headers, f"{header} header should be missing, but found: {request_headers}"

    checks = missing_header_interaction["checks"]
    missing_header_check = next((c for c in checks if c["name"] == "missing_required_header"), None)
    assert missing_header_check is not None
    assert missing_header_check["status"] == expected_status


def test_missing_required_header_default_accepts_401(ctx, cli, tmp_path):
    # Non-Authorization required headers may be rejected with 401 by auth-first middleware.
    api = ctx.openapi.apps.basic()
    cassette_path = tmp_path / "missing_token_header.yaml"

    schema_path = ctx.openapi.write_schema(
        {
            "/basic": {
                "get": {
                    "parameters": [
                        {"name": "X-API-Token", "in": "header", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    cli.run(
        str(schema_path),
        f"--url={api.base_url}/api",
        f"--report-vcr-path={cassette_path}",
        "--phases=coverage",
        "--mode=negative",
        "--checks=missing_required_header",
        "--max-examples=1",
    )

    verify_missing_required_header(cassette_path, "X-API-Token", "SUCCESS")


def test_missing_required_accept_header(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    cassette_path = tmp_path / "missing_accept_header.yaml"

    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "parameters": [
                        {
                            "name": "Accept",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "enum": ["application/json"]},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    cli.run(
        str(schema_path),
        f"--url={api.base_url}",
        f"--report-vcr-path={cassette_path}",
        "--phases=coverage",
        "--mode=negative",
        "--checks=missing_required_header",
        "--max-examples=1",
    )

    verify_missing_required_header(cassette_path, "Accept", "FAILURE")


@pytest.mark.parametrize(
    "arg",
    [
        "--header=Authorization: ABC",
        "--auth=test:test",
    ],
)
def test_missing_required_authorization_if_provided_explicitly(ctx, cli, tmp_path, arg):
    api = ctx.openapi.apps.basic()
    cassette_path = tmp_path / "missing_authorization_header.yaml"

    cli.run(
        api.schema_url,
        f"--report-vcr-path={cassette_path}",
        "--phases=coverage",
        "--mode=negative",
        "--checks=missing_required_header",
        "--max-examples=1",
        arg,
    )

    verify_missing_required_header(cassette_path, "Authorization", "SUCCESS")


@pytest.mark.parametrize(
    ("status_code", "should_raise"),
    [
        (400, False),
        (401, False),
        (403, False),
        (406, False),
        (415, False),
        (422, False),
        (200, True),
        (500, True),
    ],
)
def test_missing_required_header_default_statuses(ctx, response_factory, status_code, should_raise):
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "parameters": [{"name": "X-API-Token", "in": "header", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    response = response_factory.requests(status_code=status_code)
    case = operation.Case(
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.COVERAGE,
                data=CoveragePhaseData(
                    scenario=CoverageScenario.MISSING_PARAMETER,
                    description="Missing `X-API-Token` at header",
                    location="header",
                    parameter="X-API-Token",
                    parameter_location=ParameterLocation.HEADER,
                ),
            ),
        ),
    )
    context = check_context()
    if should_raise:
        with pytest.raises(Failure, match="header, expected 400, 401, 403, 406, 415, 422$"):
            missing_required_header(context, response, case)
    else:
        assert missing_required_header(context, response, case) is None


@pytest.mark.parametrize("path, method", [("/success", "get"), ("/basic", "post")])
def test_method_not_allowed(ctx, cli, snapshot_cli, path, method):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            path: {
                method: {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={api.base_url}",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("query", "mutated", "reported"),
    [
        ({"tag": ["a"], "page": ["2"]}, ("page",), False),
        ({"extra": "x", "page": ["2"]}, ("page",), False),
        ({"q": [], "page": ["2"]}, ("q", "page"), False),
        ({"page": ["5"]}, ("page",), True),
        ({"tag": [["a"]]}, ("tag",), True),
    ],
    ids=["after-array-parameter", "after-extra-key", "after-unsent-parameter", "invalid-element", "nested-array"],
)
def test_negative_data_rejection_single_element_array_beside_other_parameters(
    ctx, response_factory, query, mutated, reported
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"in": "query", "name": "tag", "schema": {"type": "array", "items": {"type": "string"}}},
                {"in": "query", "name": "page", "schema": {"type": "integer", "maximum": 3}},
                {"in": "query", "name": "q", "schema": {"type": "string"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            mutations=tuple(
                _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("items",), name)
                if name == "tag"
                else _mutation(OperatorKind.CHANGE_TYPE, ("type",), name)
                for name in mutated
            ),
        ),
        query=query,
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    _assert_rejection(case, response, reported=reported)


def test_negative_data_rejection_single_element_array_serialization(ctx, response_factory):
    # When a single-element array is generated for negative testing (e.g., [67] for an integer parameter),
    # it serializes to the same query string as a single integer (page=67).
    # The API correctly accepts this as valid, so negative_data_rejection should not fail.

    operation = ctx.openapi.load_operation(
        "GET /job_info/scroll",
        {
            "parameters": [
                {
                    "name": "page",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer"},
                }
            ],
            "responses": {
                "200": {"description": "Success"},
                "400": {"description": "Bad Request"},
            },
        },
    )

    # Simulate negative testing where a single-element array [67] is generated
    # for an integer parameter
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
        ),
        query={"page": [67]},  # Single-element array
    )

    # Create a successful response (200 OK)
    response = response_factory.requests(status_code=200)

    # The check should NOT raise an error because:
    # 1. The single-element array [67] serializes to "67"
    # 2. This is valid for an integer parameter
    # 3. The API correctly returns 200
    result = negative_data_rejection(
        check_context(),
        response,
        case,
    )

    # Should return None (no error) because the serialized value is valid
    assert result is None


def test_negative_data_rejection_non_mapping_query_with_another_invalid_location(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "a", "in": "query", "schema": {"type": "integer"}},
                {"name": "X-Id", "in": "header", "required": True, "schema": {"type": "integer"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter="X-Id",
            parameter_location=ParameterLocation.HEADER,
            mutations=(
                _mutation(
                    OperatorKind.CHANGE_TYPE,
                    ("type",),
                    parameter="X-Id",
                    location=ParameterLocation.HEADER,
                ),
            ),
        ),
        query="a=1",
        headers={"X-Id": "abc"},
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


@pytest.mark.parametrize(
    ("value", "reported"),
    [(["false"], True), ([-1], True), (["5"], False), ([5], False)],
    ids=["non-numeric-string", "below-minimum", "numeric-string", "valid-integer"],
)
def test_negative_data_rejection_single_element_array_element_validity(ctx, response_factory, value, reported):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "id", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"id": value},
    )
    response = response_factory.requests(status_code=200)
    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize(
    ("version", "schema"),
    [
        ("3.0.2", {"anyOf": [{"type": "integer"}]}),
        ("3.0.2", {"oneOf": [{"type": "integer", "minimum": 1}]}),
        ("3.1.0", {"enum": [12345678901234567890]}),
    ],
    ids=["any-of-integer", "one-of-integer", "enum-large-integer"],
)
def test_negative_data_rejection_single_element_array_with_untyped_schema(ctx, response_factory, version, schema):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"name": "id", "in": "query", "required": True, "schema": schema}],
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )
    value = str(schema["enum"][0]) if "enum" in schema else "5"
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"id": [value]},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def _type_mutation(name, location, new_value):
    return Mutation(
        path=(name,),
        parameter_location=location,
        schema_pointer=f"/properties/{name}",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.CHANGE_TYPE,
        keywords=("type",),
        parameter=name,
        original_value="integer",
        new_value=new_value,
    )


@pytest.mark.parametrize("other_location", [ParameterLocation.QUERY, ParameterLocation.HEADER], ids=["query", "header"])
@pytest.mark.parametrize(
    ("other_value", "other_is_negated", "reported"),
    [("not-an-int", True, True), ("7", False, False)],
    ids=["other-invalid", "other-valid"],
)
def test_negative_data_rejection_valid_array_element_beside_invalid_parameter(
    ctx, response_factory, other_location, other_value, other_is_negated, reported
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "a", "in": "query", "required": True, "schema": {"type": "integer"}},
                {"name": "b", "in": other_location.value, "required": True, "schema": {"type": "integer"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    mutations = [_type_mutation("a", ParameterLocation.QUERY, "array")]
    components = {"query": GenerationMode.NEGATIVE}
    if other_is_negated:
        mutations.append(_type_mutation("b", other_location, "string"))
        components[other_location.container_name] = GenerationMode.NEGATIVE
    query = {"a": [5]}
    headers = None
    if other_location == ParameterLocation.QUERY:
        query["b"] = other_value
    else:
        headers = {"b": other_value}
    case = operation.Case(
        _meta=build_metadata(**components, generation_modes=[GenerationMode.NEGATIVE], mutations=tuple(mutations)),
        query=query,
        headers=headers,
    )
    response = response_factory.requests(status_code=200)
    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize("page", ["null", ["null", "null"]], ids=["single", "repeated"])
def test_negative_data_rejection_nullable_query_beside_invalid_header(ctx, app_runner, page):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "page",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1, "nullable": True},
                        },
                        {
                            "name": "X-Token",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    case = schema["/items"]["GET"].Case(
        query={"page": page},
        headers={"X-Token": ""},
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE, headers=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]
        ),
    )

    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), case.call(), case)


def test_negative_data_rejection_multiple_mutations_name_parameters(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /api/items",
        {
            "parameters": [
                {"name": "kind", "in": "query", "required": True, "schema": {"type": "string", "minLength": 2}},
                {"name": "tag", "in": "query", "required": False, "schema": {"type": "string", "minLength": 2}},
            ],
            "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
        },
    )

    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Violates `minLength` at /properties/kind\nViolates `minLength` at /properties/tag",
            parameter_location=ParameterLocation.QUERY,
            mutations=tuple(
                Mutation(
                    path=(),
                    parameter_location=ParameterLocation.QUERY,
                    schema_pointer=f"/properties/{name}",
                    channel=MutationChannel.SCHEMA,
                    operator=OperatorKind.NEGATE_CONSTRAINTS,
                    keywords=("minLength",),
                    parameter=name,
                    original_value=None,
                    new_value=None,
                )
                for name in ("kind", "tag")
            ),
        ),
        query={"kind": "", "tag": ""},
    )

    with pytest.raises(AcceptedNegativeData) as exc:
        negative_data_rejection(check_context(), response_factory.requests(status_code=200), case)

    assert "Invalid component: parameters `kind`, `tag` in query" in str(exc.value)


@pytest.mark.parametrize(
    ("value", "reported"),
    [
        ({"7": "x"}, False),
        (7, False),
        (True, False),
        ({"a b": "x"}, True),
    ],
    ids=["object-collapse-valid", "scalar-valid", "boolean-valid", "object-collapse-invalid"],
)
def test_negative_data_rejection_query_string_parameter_wire_form(ctx, response_factory, value, reported):
    # A wrong-typed query value can still reach the server as text that satisfies the declared schema.
    operation = ctx.openapi.load_operation(
        "GET /api/items",
        {
            "parameters": [
                {
                    "name": "kind",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "^\\S+$"},
                }
            ],
            "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
        },
    )

    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid type object (expected string)",
            parameter="kind",
            parameter_location=ParameterLocation.QUERY,
        ),
        query={"kind": value},
    )
    response = response_factory.requests(status_code=200)

    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize(
    "parameter_schema",
    [{"type": "boolean"}, {"type": "string", "enum": ["asc"]}, {"type": "string", "minLength": 1}],
    ids=["boolean", "enum", "min-length"],
)
@pytest.mark.parametrize("allow_empty_value", [True, False, None], ids=["allowed", "forbidden", "default"])
def test_negative_data_rejection_query_allow_empty_value(ctx, response_factory, parameter_schema, allow_empty_value):
    parameter = {"name": "filter", "in": "query", "required": True, "schema": parameter_schema}
    if allow_empty_value is not None:
        parameter["allowEmptyValue"] = allow_empty_value
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [parameter],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid query parameter",
            parameter="filter",
            parameter_location=ParameterLocation.QUERY,
        ),
        query={"filter": ""},
    )
    response = response_factory.requests(status_code=200)

    _assert_rejection(case, response, reported=allow_empty_value is not True)


@pytest.mark.parametrize(
    ("value", "reported"),
    [
        (EncodedPath("18"), False),
        (EncodedPath("3,4"), False),
        (EncodedPath("1.5"), True),
        (EncodedPath("a"), True),
    ],
    ids=["integer", "comma-joined-integers", "float", "non-numeric-string"],
)
def test_negative_data_rejection_path_array_negated_to_scalar(ctx, response_factory, value, reported):
    # `simple` style joins items with commas, so `18` is the wire form of the valid `[18]`.
    operation = ctx.openapi.load_operation(
        "GET /api/items/{ids}",
        {
            "parameters": [
                {
                    "name": "ids",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                }
            ],
            "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="violates `type` at /properties/ids (was array, became integer)",
            parameter="ids",
            parameter_location=ParameterLocation.PATH,
            mutations=(
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="ids", location=ParameterLocation.PATH),
            ),
        ),
        path_parameters={"ids": value},
    )
    response = response_factory.requests(status_code=200)

    _assert_rejection(case, response, reported=reported)


@pytest.mark.parametrize(
    ("style", "path_parameters", "reported"),
    [
        ("simple", {"kind": 5, "ids": EncodedPath("a,b")}, False),
        ("simple", {"kind": EncodedPath("x"), "ids": EncodedPath("a,b")}, False),
        ("simple", {"kind": EncodedPath("x"), "ids": EncodedPath("a,b,c")}, True),
        ("label", {"kind": EncodedPath("x"), "ids": EncodedPath("a,b")}, True),
    ],
    ids=["after-non-string-value", "after-scalar-parameter", "too-many-items", "label-style"],
)
def test_negative_data_rejection_path_array_beside_other_path_parameter(
    ctx, response_factory, style, path_parameters, reported
):
    operation = ctx.openapi.load_operation(
        "GET /api/items/{kind}/{ids}",
        {
            "parameters": [
                {"name": "kind", "in": "path", "required": True, "schema": {"type": "string"}},
                {
                    "name": "ids",
                    "in": "path",
                    "required": True,
                    "style": style,
                    "schema": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                },
            ],
            "responses": {"200": {"description": "Success"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(path_parameters=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        path_parameters=path_parameters,
    )
    response = response_factory.requests(status_code=200)

    _assert_rejection(case, response, reported=reported)


def test_negative_data_rejection_path_string_numeric_serialization_with_other_negation(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "POST /api/run/{id}",
        {
            "parameters": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                {"name": "key", "in": "query", "required": False, "schema": {"type": "integer"}},
            ],
            "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
        },
    )

    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid type string (expected integer)",
            parameter="id",
            parameter_location=ParameterLocation.PATH,
        ),
        path_parameters={"id": "%2B1"},
        query={"key": "abc"},
    )
    response = response_factory.requests(status_code=200)

    with pytest.raises(Failure):
        negative_data_rejection(
            check_context(),
            response,
            case,
        )


def test_response_schema_conformance_with_surrogate_chars_in_response(response_factory, ctx):
    # The JSON escape \uDCF3 is a lone low surrogate; Python's json.loads accepts it and
    # produces a Python str containing the lone surrogate '\udcf3'. jsonschema_rs then
    # raises ValueError  when it tries to UTF-8-encode that string.
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"type": "string"}}},
                }
            }
        },
    )
    case = operation.Case()
    response = response_factory.requests(content=b'"\\udcf3"')
    response = Response.from_requests(response, True)

    with pytest.raises(MalformedJson) as exc_info:
        response_schema_conformance(
            check_context(),
            response,
            case,
        )
    failure = exc_info.value
    # document should be the raw JSON text
    assert failure.document == '"\\udcf3"'
    # \udcf3 starts at index 1 in the document
    assert failure.position == 1
    assert failure.lineno == 1
    assert failure.colno == 2


def test_response_validation_reports_surrogate_returned_by_deserializer(ctx, response_factory):
    @schemathesis.deserializer("application/vnd.surrogate")
    def deserialize(_ctx, response):
        return "\udcf3"

    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/vnd.surrogate": {"schema": {"type": "string"}}},
                }
            }
        },
    )
    response = Response.from_requests(
        response_factory.requests(content=b"x", content_type="application/vnd.surrogate"), True
    )
    with pytest.raises(MalformedJson) as exc_info:
        operation.validate_response(response)
    failure = exc_info.value
    assert (failure.document, failure.position, failure.lineno, failure.colno) == ("x", 0, 1, 1)


def test_response_schema_conformance_reports_malformed_response_media_type(response_factory, ctx):
    operation = ctx.openapi.load_operation(
        "GET /foo", {"responses": {"200": {"description": "OK", "content": {"text": {"schema": {"type": "object"}}}}}}
    )
    case = operation.Case()
    response = response_factory.requests(content=b'{"a": 1}', content_type="text")

    with pytest.raises(FailureGroup) as exc_info:
        case.validate_response(response, checks=[response_schema_conformance])

    failures = exc_info.value.exceptions
    assert [type(failure) for failure in failures] == [ContentDeserializationError]
    assert failures[0].title == "Content deserialization error"
    assert "Malformed media type: `text`" in failures[0].message


BODY_SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
OPENAPI_3_RESPONSES = {"200": {"description": "OK", "content": {"application/json": {"schema": BODY_SCHEMA}}}}
SWAGGER_2_RESPONSES = {"200": {"description": "OK", "schema": BODY_SCHEMA}}


def _operation_with_json_response(ctx, method, version):
    if version == "2.0":
        definition = {"produces": ["application/json"], "responses": SWAGGER_2_RESPONSES}
    else:
        definition = {"responses": OPENAPI_3_RESPONSES}
    schema = ctx.openapi.load_schema({"/x": {method: definition}}, version=version)
    return schema["/x"][method.upper()]


@pytest.mark.parametrize("version", ["3.0.2", "2.0"], ids=["openapi-3", "swagger-2"])
def test_response_schema_conformance_skips_empty_head_body(ctx, response_factory, version):
    case = _operation_with_json_response(ctx, "head", version).Case()
    response = Response.from_requests(response_factory.requests(content=b"", method="HEAD"), True)
    assert response_schema_conformance(check_context(), response, case) is None


@pytest.mark.parametrize("version", ["3.0.2", "2.0"], ids=["openapi-3", "swagger-2"])
def test_response_schema_conformance_rejects_empty_get_body(ctx, response_factory, version):
    case = _operation_with_json_response(ctx, "get", version).Case()
    response = Response.from_requests(response_factory.requests(content=b"", method="GET"), True)
    with pytest.raises(MalformedJson, match="Expecting value"):
        response_schema_conformance(check_context(), response, case)


def _operation_with_ref_sibling_response(ctx, version, sibling):
    reference = {"$ref": "#/definitions/Base" if version == "2.0" else "#/components/schemas/Base", **sibling}
    base = {"type": "object", "properties": {"id": {"type": "integer"}}}
    if version == "2.0":
        definition = {
            "produces": ["application/json"],
            "responses": {"200": {"description": "OK", "schema": reference}},
        }
        schema = ctx.openapi.load_schema({"/x": {"get": definition}}, version=version, definitions={"Base": base})
    else:
        definition = {
            "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": reference}}}}
        }
        schema = ctx.openapi.load_schema(
            {"/x": {"get": definition}}, version=version, components={"schemas": {"Base": base}}
        )
    return schema["/x"]["GET"]


@pytest.mark.parametrize("version", ["3.0.2", "2.0"], ids=["openapi-3.0", "swagger-2"])
def test_response_schema_conformance_ignores_keywords_next_to_ref(ctx, response_factory, version):
    case = _operation_with_ref_sibling_response(ctx, version, {"required": ["extra"], "minProperties": 3}).Case()
    response = Response.from_requests(response_factory.requests(content=b'{"id": 1}'), True)
    assert response_schema_conformance(check_context(), response, case) is None


@pytest.mark.parametrize(
    ("version", "keyword"),
    [("3.0.2", "nullable"), ("2.0", "x-nullable")],
    ids=["openapi-3.0", "swagger-2"],
)
def test_response_schema_conformance_honors_nullable_next_to_ref(ctx, response_factory, version, keyword):
    case = _operation_with_ref_sibling_response(ctx, version, {keyword: True, "required": ["extra"]}).Case()
    response = Response.from_requests(response_factory.requests(content=b"null"), True)
    assert response_schema_conformance(check_context(), response, case) is None


def test_response_schema_conformance_applies_keywords_next_to_ref_in_openapi_31(ctx, response_factory):
    case = _operation_with_ref_sibling_response(ctx, "3.1.0", {"required": ["extra"]}).Case()
    response = Response.from_requests(response_factory.requests(content=b'{"id": 1}'), True)
    with pytest.raises(JsonSchemaError, match='"extra" is a required property'):
        response_schema_conformance(check_context(), response, case)


@pytest.mark.parametrize(
    ("response_key", "expected_note"),
    [
        ("404", "Validated against the response schema for status code 404."),
        ("4XX", "Validated against the response schema for `4XX` (status code 404)."),
        ("default", "Validated against the `default` response schema (status code 404)."),
    ],
    ids=["exact", "wildcard", "default"],
)
def test_response_schema_conformance_names_matched_response(ctx, response_factory, response_key, expected_note):
    # Two definitions share a `code` field with different types; the note must state which response schema was used.
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"properties": {"code": {"type": "string"}}}}},
                },
                response_key: {
                    "description": "Error",
                    "content": {"application/json": {"schema": {"properties": {"code": {"type": "integer"}}}}},
                },
            }
        },
    )
    case = operation.Case()
    response = response_factory.requests(status_code=404, content=b'{"code": "213"}')
    response = Response.from_requests(response, True)

    with pytest.raises(Failure) as exc_info:
        response_schema_conformance(_CHECK_CTX, response, case)
    assert expected_note in exc_info.value.message


_CHECK_CTX = check_context()

_DATE_TIME_PATHS = {
    "/test": {
        "get": {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"type": "string", "format": "date-time"}}},
                }
            }
        }
    }
}
# `str(datetime)` renders a space separator, not the RFC 3339 `T`.
_STR_DATETIME_RESPONSE = b'"2018-03-26 14:43:59.004000+00:00"'


def test_response_schema_conformance_invalid_format_fails_by_default(ctx, response_factory):
    schema = ctx.openapi.load_schema(_DATE_TIME_PATHS)
    case = schema["/test"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(content=_STR_DATETIME_RESPONSE), True)

    with pytest.raises(JsonSchemaError, match='is not a "date-time"'):
        response_schema_conformance(_CHECK_CTX, response, case)


# A `writeOnly` property is rewritten to a schema nothing satisfies; the message must still read as English.
def test_response_schema_conformance_forbidden_property_message(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"secret": {"type": "string", "writeOnly": True}},
                            }
                        }
                    },
                }
            }
        },
    )
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(content=b'{"secret": "s"}'), True)

    with pytest.raises(JsonSchemaError) as exc_info:
        response_schema_conformance(_CHECK_CTX, response, case)
    assert exc_info.value.message.startswith('Property "secret" is not allowed')


@pytest.mark.parametrize(
    ("property_schema", "object_type", "version"),
    [
        pytest.param({"$ref": "#/components/schemas/Secret"}, "object", "3.0.2", id="reference"),
        pytest.param({"allOf": [{"$ref": "#/components/schemas/Secret"}]}, "object", "3.0.2", id="allOf-reference"),
        pytest.param({"allOf": [{"writeOnly": True}, {"type": "string"}]}, "object", "3.0.2", id="allOf-inline"),
        pytest.param(
            {"anyOf": [{"$ref": "#/components/schemas/Secret"}, {"type": "null"}]},
            "object",
            "3.1.0",
            id="anyOf-nullable",
        ),
        pytest.param(
            {"$ref": "#/components/schemas/Secret", "nullable": True},
            "object",
            "3.0.2",
            id="openapi-30-nullable",
        ),
        pytest.param({"writeOnly": True}, None, "3.0.2", id="untyped-object"),
        pytest.param({"writeOnly": True}, ["object", "null"], "3.1.0", id="object-null-union"),
    ],
)
def test_response_schema_conformance_rejects_nested_write_only_property(
    ctx, response_factory, property_schema, object_type, version
):
    response_schema = {"properties": {"secret": property_schema}}
    if object_type is not None:
        response_schema["type"] = object_type
    operation = ctx.openapi.load_operation(
        "GET /test",
        {"responses": json_ok(response_schema)},
        version=version,
        components={"schemas": {"Secret": {"type": "string", "writeOnly": True}}},
    )
    response = response_factory.requests(content=b'{"secret": "s"}')

    with pytest.raises(FailureGroup, match='Property "secret" is not allowed'):
        operation.Case().validate_response(response, checks=[response_schema_conformance])


@pytest.mark.parametrize(
    ("property_schema", "object_type", "version"),
    [
        pytest.param({"$ref": "#/components/schemas/Secret"}, "object", "3.0.2", id="reference"),
        pytest.param({"allOf": [{"$ref": "#/components/schemas/Secret"}]}, "object", "3.0.2", id="allOf-reference"),
        pytest.param({"allOf": [{"writeOnly": True}, {"type": "string"}]}, "object", "3.0.2", id="allOf-inline"),
        pytest.param(
            {"anyOf": [{"$ref": "#/components/schemas/Secret"}, {"type": "null"}]},
            "object",
            "3.1.0",
            id="anyOf-nullable",
        ),
        pytest.param(
            {"$ref": "#/components/schemas/Secret", "nullable": True},
            "object",
            "3.0.2",
            id="openapi-30-nullable",
        ),
        pytest.param({"writeOnly": True}, None, "3.0.2", id="untyped-object"),
        pytest.param({"writeOnly": True}, ["object", "null"], "3.1.0", id="object-null-union"),
    ],
)
def test_response_schema_conformance_accepts_missing_nested_write_only_property(
    ctx, response_factory, property_schema, object_type, version
):
    # `required` on a writeOnly field applies to requests only.
    response_schema = {"properties": {"secret": property_schema}, "required": ["secret"]}
    if object_type is not None:
        response_schema["type"] = object_type
    operation = ctx.openapi.load_operation(
        "GET /test",
        {"responses": json_ok(response_schema)},
        version=version,
        components={"schemas": {"Secret": {"type": "string", "writeOnly": True}}},
    )
    response = response_factory.requests(content=b"{}")

    assert operation.Case().validate_response(response, checks=[response_schema_conformance]) is None


def test_response_schema_conformance_reports_boolean_branch_rejection(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"allOf": [{"type": "object"}, False]}}},
                }
            }
        },
        version="3.1.0",
    )
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(content=b"{}"), True)

    with pytest.raises(JsonSchemaError) as exc:
        response_schema_conformance(_CHECK_CTX, response, case)
    assert exc.value.message == (
        "False schema does not allow {}\n\n"
        "Validated against the response schema for status code 200.\n\n"
        "Schema at /allOf:\n\n    false\n\nValue:\n\n    {}"
    )


def test_response_schema_conformance_validate_formats_disabled(ctx, response_factory):
    config = SchemathesisConfig.from_dict({"checks": {"response_schema_conformance": {"validate-formats": False}}})
    schema = ctx.openapi.load_schema(_DATE_TIME_PATHS, config=config)
    case = schema["/test"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(content=_STR_DATETIME_RESPONSE), True)

    assert response_schema_conformance(_CHECK_CTX, response, case) is None


def _discriminator_schema(ctx, *, discriminator, version="3.0.2"):
    return ctx.openapi.load_schema(
        {
            "/pets": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "anyOf": [
                                            {"$ref": "#/components/schemas/Cat"},
                                            {"$ref": "#/components/schemas/Dog"},
                                        ],
                                        "discriminator": discriminator,
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
        version=version,
        components={
            "schemas": {
                "Cat": {"type": "object", "properties": {"petType": {"type": "string"}}},
                "Dog": {"type": "object", "properties": {"petType": {"type": "string"}}},
            }
        },
    )


@pytest.mark.parametrize(
    ("body", "discriminator", "should_fail"),
    [
        # Implicit mapping: schema name matches discriminator value
        ({"petType": "Cat"}, {"propertyName": "petType"}, False),
        ({"petType": "Dog"}, {"propertyName": "petType"}, False),
        ({"petType": "Fish"}, {"propertyName": "petType"}, True),
        # Explicit mapping values are valid
        (
            {"petType": "feline"},
            {
                "propertyName": "petType",
                "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
            },
            False,
        ),
        # Implicit schema names remain valid even when explicit mapping is present
        (
            {"petType": "Cat"},
            {
                "propertyName": "petType",
                "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
            },
            False,
        ),
        # Unknown value fails even when explicit mapping exists
        (
            {"petType": "Fish"},
            {
                "propertyName": "petType",
                "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
            },
            True,
        ),
        # Missing discriminator property: skip check (let JSON schema handle required fields)
        ({}, {"propertyName": "petType"}, False),
        # No propertyName in discriminator: skip check
        ({"petType": "Fish"}, {}, False),
        # Open API 3.2 routes unknown values to the `defaultMapping` schema
        ({"petType": "Fish"}, {"propertyName": "petType", "defaultMapping": "#/components/schemas/Dog"}, False),
    ],
    ids=[
        "implicit-valid-cat",
        "implicit-valid-dog",
        "implicit-invalid-fish",
        "explicit-valid-feline",
        "explicit-and-implicit-valid-cat",
        "explicit-invalid-fish",
        "missing-property-skip",
        "no-property-name-skip",
        "default-mapping-unknown-value",
    ],
)
def test_response_schema_conformance_discriminator(ctx, response_factory, body, discriminator, should_fail):
    schema = _discriminator_schema(ctx, discriminator=discriminator)
    operation = schema["/pets"]["GET"]
    case = operation.Case()
    response = response_factory.requests(content=json.dumps(body).encode())
    response = Response.from_requests(response, True)

    if should_fail:
        with pytest.raises(DiscriminatorMismatch) as exc_info:
            response_schema_conformance(_CHECK_CTX, response, case)
        assert exc_info.value.title == "Unknown discriminator value"
    else:
        assert response_schema_conformance(_CHECK_CTX, response, case) is None


def test_response_schema_conformance_discriminator_whole_file_ref(tmp_path, response_factory):
    (tmp_path / "cat.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"petType": {"type": "string"}, "name": {"type": "string"}},
                "required": ["petType", "name"],
            }
        )
    )
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.2",
                "info": {"title": "Pets", "version": "1.0"},
                "paths": {
                    "/pets": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "anyOf": [
                                                    {"$ref": "cat.json"},
                                                    {"$ref": "#/components/schemas/Dog"},
                                                ],
                                                "discriminator": {"propertyName": "petType"},
                                            }
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
                "components": {
                    "schemas": {
                        "Dog": {
                            "type": "object",
                            "properties": {"petType": {"type": "string"}, "bark": {"type": "boolean"}},
                            "required": ["petType", "bark"],
                        }
                    }
                },
            }
        )
    )
    operation = schemathesis.openapi.from_path(tmp_path / "openapi.json")["/pets"]["GET"]

    assert (
        operation.validate_response(response_factory.requests(content=b'{"petType": "cat", "name": "Misty"}')) is None
    )
    with pytest.raises(Failure, match="known schema values: 'Dog', 'cat'"):
        operation.validate_response(response_factory.requests(content=b'{"petType": "cow", "name": "Misty"}'))


def test_response_schema_conformance_discriminator_ignores_nameless_reference(ctx, response_factory):
    # A reference to an `$id` ending in `/` carries no implicit name, so it adds no known value.
    operation = ctx.openapi.load_operation(
        "GET /pets",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "anyOf": [
                                    {"$ref": "https://example.invalid/"},
                                    {"$ref": "#/components/schemas/Dog"},
                                ],
                                "discriminator": {"propertyName": "petType"},
                            }
                        }
                    },
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "Root": {"$id": "https://example.invalid/", "type": "object"},
                "Dog": {"type": "object"},
            }
        },
    )
    with pytest.raises(Failure, match="known schema values: 'Dog'$"):
        operation.validate_response(response_factory.requests(content=b'{"petType": ""}'))


JSON_SEQUENCE_ITEM = {
    "type": "object",
    "properties": {"id": {"type": "integer"}},
    "required": ["id"],
    "additionalProperties": False,
}


def _validated_sequence(operation, response_factory, media_type, body):
    response = response_factory.core(content=body.encode(), headers={"content-type": [media_type]})
    try:
        operation.validate_response(response)
    except FailureGroup as group:
        failures = list(group.exceptions)
    except Failure as failure:
        failures = [failure]
    else:
        failures = []
    return [
        (type(failure), list(failure.instance_path))
        if isinstance(failure, JsonSchemaError)
        else (type(failure), (failure.lineno, failure.colno))
        for failure in failures
    ]


@pytest.mark.parametrize(
    ("version", "media_type", "media_type_object", "body", "expected"),
    [
        ("3.2.0", "application/jsonl", {"itemSchema": JSON_SEQUENCE_ITEM}, '{"id":1}\r\n\r\n{"id":2}\n', []),
        ("3.2.0", "application/jsonl", {"itemSchema": JSON_SEQUENCE_ITEM}, '﻿{"id":1}\n', []),
        (
            "3.2.0",
            "application/jsonl",
            {"itemSchema": JSON_SEQUENCE_ITEM},
            '{"id":1}\n{"id":"x"}\n',
            [(JsonSchemaError, [1, "id"])],
        ),
        (
            "3.2.0",
            "application/x-ndjson",
            {"itemSchema": JSON_SEQUENCE_ITEM},
            '{"id":1}\n{"id":"x"}\n',
            [(JsonSchemaError, [1, "id"])],
        ),
        (
            "3.2.0",
            "application/json-seq",
            {"itemSchema": JSON_SEQUENCE_ITEM},
            '\x1e{"id":1}\n\x1e{"id":"x"}\n',
            [(JsonSchemaError, [1, "id"])],
        ),
        (
            "3.2.0",
            "application/geo+json-seq",
            {"itemSchema": JSON_SEQUENCE_ITEM},
            '\x1e{"id":"x"}\n',
            [(JsonSchemaError, [0, "id"])],
        ),
        (
            "3.2.0",
            "application/jsonl",
            {"itemSchema": JSON_SEQUENCE_ITEM, "schema": {"maxItems": 1}},
            '{"id":1}\n{"id":2}\n',
            [(JsonSchemaError, [])],
        ),
        (
            "3.2.0",
            "application/jsonl",
            {"itemSchema": JSON_SEQUENCE_ITEM},
            '{"id":1}\n{"id":2}\n{oops\n',
            [(MalformedJson, (3, 2))],
        ),
        (
            "3.2.0",
            "application/jsonl",
            {
                "itemSchema": {
                    "properties": {
                        "payload": {"contentMediaType": "application/json", "contentSchema": {"type": "object"}}
                    }
                }
            },
            '{"payload":{"a":1}}\n{"payload":"[1]"}\n',
            [],
        ),
        ("3.1.0", "application/x-ndjson", {"schema": JSON_SEQUENCE_ITEM}, '{"id":1}\n{"id":2}\n', []),
        (
            "3.1.0",
            "application/x-ndjson",
            {"schema": {"type": "array", "items": {"type": "string"}}},
            '{"id":1}\n',
            [],
        ),
    ],
    ids=[
        "jsonl-valid",
        "jsonl-byte-order-mark",
        "jsonl-bad-item",
        "ndjson-bad-item",
        "json-seq-bad-item",
        "json-seq-suffix-bad-item",
        "stream-schema",
        "malformed-record-position",
        "content-keywords-annotate-only",
        "openapi-3-1-item-schema-unvalidated",
        "openapi-3-1-array-schema-unvalidated",
    ],
)
def test_response_schema_conformance_json_sequence(
    ctx, response_factory, version, media_type, media_type_object, body, expected
):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {"responses": {"200": {"description": "OK", "content": {media_type: media_type_object}}}},
        version=version,
    )
    assert _validated_sequence(operation, response_factory, media_type, body) == expected


@pytest.mark.parametrize(
    ("version", "definition", "media_type"),
    [
        (
            "2.0",
            {
                "produces": ["application/x-ndjson"],
                "responses": {"200": {"description": "OK", "schema": JSON_SEQUENCE_ITEM}},
            },
            "application/x-ndjson",
        ),
        (
            "3.0.2",
            {"responses": {"200": {"description": "OK", "content": {"*/*": {"schema": JSON_SEQUENCE_ITEM}}}}},
            "application/x-ndjson",
        ),
    ],
    ids=["swagger-2-produces", "wildcard-media-type"],
)
def test_response_schema_conformance_json_sequence_without_item_schema(
    ctx, response_factory, version, definition, media_type
):
    operation = ctx.openapi.load_operation("GET /items", definition, version=version)
    assert _validated_sequence(operation, response_factory, media_type, '{"id":1}\n{"id":2}\n') == []


def test_response_schema_conformance_json_sequence_discriminator(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "GET /pets",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/jsonl": {"itemSchema": {"$ref": "#/components/schemas/Pet"}}},
                }
            }
        },
        version="3.2.0",
        components={
            "schemas": {
                "Pet": {
                    "anyOf": [{"$ref": "#/components/schemas/Cat"}, {"$ref": "#/components/schemas/Dog"}],
                    "discriminator": {"propertyName": "kind"},
                },
                "Cat": {"type": "object"},
                "Dog": {"type": "object"},
            }
        },
    )
    with pytest.raises(DiscriminatorMismatch, match="value 'Bird'"):
        operation.validate_response(
            response_factory.core(
                content=b'{"kind": "Cat"}\n{"kind": "Bird"}\n', headers={"content-type": ["application/jsonl"]}
            )
        )


def test_response_schema_conformance_discriminator_boolean_schema(ctx, response_factory):
    # Boolean schemas (true/false) in anyOf/oneOf are valid in OpenAPI 3.1.
    # The boolean item is skipped during implicit mapping extraction; only $ref items contribute.
    operation = ctx.openapi.load_operation(
        "GET /pets",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "anyOf": [
                                    {"$ref": "#/components/schemas/Cat"},
                                    True,
                                ],
                                "discriminator": {"propertyName": "petType"},
                            }
                        }
                    },
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "Cat": {"type": "object", "properties": {"petType": {"type": "string"}}},
            }
        },
    )
    case = operation.Case()

    valid = response_factory.requests(content=b'{"petType": "Cat"}')
    assert response_schema_conformance(_CHECK_CTX, Response.from_requests(valid, True), case) is None

    invalid = response_factory.requests(content=b'{"petType": "Fish"}')
    with pytest.raises(DiscriminatorMismatch) as exc_info:
        response_schema_conformance(_CHECK_CTX, Response.from_requests(invalid, True), case)
    assert exc_info.value.title == "Unknown discriminator value"


def _single_response_schema(ctx, content):
    return ctx.openapi.load_schema(
        {"/test": {"get": {"responses": {"200": {"description": "OK", "content": content}}}}}
    )


def test_response_schema_conformance_skips_malformed_media_type_key(ctx, response_factory):
    schema = _single_response_schema(
        ctx,
        {
            "not a media type": {"schema": {"type": "string"}},
            "application/*": {"schema": {"type": "integer"}},
        },
    )
    case = schema["/test"]["GET"].Case()
    valid = Response.from_requests(response_factory.requests(content=b"42"), True)
    assert response_schema_conformance(_CHECK_CTX, valid, case) is None

    invalid = Response.from_requests(response_factory.requests(content=b'"text"'), True)
    with pytest.raises(JsonSchemaError, match='is not of type "integer"'):
        response_schema_conformance(_CHECK_CTX, invalid, case)


def test_response_schema_conformance_ignores_non_object_media_type_entry(ctx, response_factory):
    schema = _single_response_schema(ctx, {"application/json": "not an object"})
    case = schema["/test"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(content=b'"anything"'), True)
    assert response_schema_conformance(_CHECK_CTX, response, case) is None


def test_response_schema_conformance_discriminator_inline_branch(ctx, response_factory):
    schema = _single_response_schema(
        ctx,
        {
            "application/json": {
                "schema": {
                    "oneOf": [{"type": "object", "properties": {"petType": {"type": "string"}}}],
                    "discriminator": {"propertyName": "petType", "mapping": {"cat": "#/components/schemas/Cat"}},
                }
            }
        },
    )
    case = schema["/test"]["GET"].Case()
    valid = Response.from_requests(response_factory.requests(content=b'{"petType": "cat"}'), True)
    assert response_schema_conformance(_CHECK_CTX, valid, case) is None

    invalid = Response.from_requests(response_factory.requests(content=b'{"petType": "fish"}'), True)
    with pytest.raises(DiscriminatorMismatch) as exc_info:
        response_schema_conformance(_CHECK_CTX, invalid, case)
    assert exc_info.value.title == "Unknown discriminator value"


_KIND_BRANCHES = [{"$ref": "#/components/schemas/Cat"}, {"$ref": "#/components/schemas/Dog"}]
_KIND_MAPPING = {"propertyName": "kind", "mapping": {"cat": "#/components/schemas/Cat"}}


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
@pytest.mark.parametrize(
    ("response_schema", "body", "should_fail"),
    [
        ({"$ref": "#/components/schemas/AnyPet"}, {"kind": "parrot"}, True),
        ({"$ref": "#/components/schemas/OneOfPet"}, {"kind": "parrot"}, True),
        ({"$ref": "#/components/schemas/AnyPet"}, {"kind": "Cat"}, False),
        (
            {"type": "array", "items": {"anyOf": _KIND_BRANCHES, "discriminator": _KIND_MAPPING}},
            [{"kind": "parrot"}],
            True,
        ),
        (
            {"type": "array", "items": {"anyOf": _KIND_BRANCHES, "discriminator": _KIND_MAPPING}},
            [{"kind": "cat"}],
            False,
        ),
        (
            {"type": "object", "properties": {"pet": {"$ref": "#/components/schemas/OneOfPet"}}},
            {"pet": {"kind": "parrot"}},
            True,
        ),
        (
            {"type": "object", "properties": {"pet": {"$ref": "#/components/schemas/OneOfPet"}}},
            {"pet": {"kind": "Dog"}},
            False,
        ),
        (
            {"$ref": "#/components/schemas/Tree"},
            {"pets": [{"kind": "Cat"}], "children": [{"pets": [{"kind": "parrot"}]}]},
            True,
        ),
        (
            {"$ref": "#/components/schemas/Tree"},
            {"pets": [{"kind": "Cat"}], "children": [{"pets": [{"kind": "Dog"}]}]},
            False,
        ),
    ],
    ids=[
        "ref-any-of-invalid",
        "ref-one-of-invalid",
        "ref-component-name-valid",
        "array-items-invalid",
        "array-items-mapping-valid",
        "property-invalid",
        "property-valid",
        "recursive-invalid",
        "recursive-valid",
    ],
)
def test_response_schema_conformance_discriminator_nested(
    ctx, response_factory, version, response_schema, body, should_fail
):
    operation = ctx.openapi.load_operation(
        "GET /pets",
        {"responses": {"200": {"description": "OK", "content": {"application/json": {"schema": response_schema}}}}},
        version=version,
        components={
            "schemas": {
                "Cat": {"type": "object", "properties": {"kind": {"type": "string"}}},
                "Dog": {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["barks"]},
                "AnyPet": {"anyOf": _KIND_BRANCHES, "discriminator": {"propertyName": "kind"}},
                "OneOfPet": {"oneOf": _KIND_BRANCHES, "discriminator": {"propertyName": "kind"}},
                "Tree": {
                    "type": "object",
                    "properties": {
                        "pets": {"type": "array", "items": {"$ref": "#/components/schemas/AnyPet"}},
                        "children": {"type": "array", "items": {"$ref": "#/components/schemas/Tree"}},
                    },
                },
            }
        },
    )
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(content=json.dumps(body).encode()), True)

    if should_fail:
        with pytest.raises(Failure) as exc_info:
            response_schema_conformance(_CHECK_CTX, response, case)
        assert exc_info.value.title == "Unknown discriminator value"
    else:
        assert response_schema_conformance(_CHECK_CTX, response, case) is None


def test_response_schema_conformance_discriminator_walk_handles_shared_recursive_data(ctx):
    # YAML anchors can make a parsed response body share and nest the same object.
    operation = ctx.openapi.load_operation(
        "GET /pets",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "first": {"$ref": "#/components/schemas/Node"},
                                    "second": {"$ref": "#/components/schemas/Node"},
                                },
                            }
                        }
                    },
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "child": {"$ref": "#/components/schemas/Node"},
                        "passthrough": True,
                    },
                    "discriminator": {"propertyName": "kind", "mapping": {"node": "#/components/schemas/Node"}},
                }
            }
        },
    )
    definition = operation.responses.find_by_status_code(200)
    assert definition is not None
    resolved = definition.get_schema("application/json")
    assert resolved.has_discriminator is True
    shared = {"kind": "node", "passthrough": 1}
    shared["child"] = shared

    assert _check_discriminator(operation, {"first": shared, "second": shared}, resolved) is None


@pytest.mark.parametrize("array_schema", [{}, {"items": True}, {"items": []}], ids=["absent", "boolean", "list"])
def test_response_schema_conformance_discriminator_walk_ignores_non_object_items(ctx, array_schema):
    operation = ctx.openapi.load_operation(
        "GET /pets",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "values": {"type": "array", **array_schema},
                                    "pet": {
                                        "type": "object",
                                        "discriminator": {
                                            "propertyName": "kind",
                                            "mapping": {"cat": "#/components/schemas/Cat"},
                                        },
                                    },
                                },
                            }
                        }
                    },
                }
            }
        },
        version="3.1.0",
        components={"schemas": {"Cat": {"type": "object"}}},
    )
    definition = operation.responses.find_by_status_code(200)
    assert definition is not None

    assert (
        _check_discriminator(
            operation,
            {"values": [{}], "pet": {"kind": "cat"}},
            definition.get_schema("application/json"),
        )
        is None
    )


def test_response_schema_conformance_keeps_large_instance_when_truncation_is_disabled(ctx, response_factory):
    value = "x" * 200
    operation = ctx.openapi.load_operation(
        "GET /value",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"type": "string", "maxLength": 1}}},
                }
            }
        },
        config=SchemathesisConfig.from_dict({"output": {"truncation": {"enabled": False}}}),
    )
    response = Response.from_requests(response_factory.requests(content=json.dumps(value).encode()), True)

    with pytest.raises(JsonSchemaError) as exc_info:
        response_schema_conformance(_CHECK_CTX, response, operation.Case())
    assert exc_info.value.validation_message == f'"{value}" is longer than 1 character'


def test_response_schema_conformance_discriminator_with_file_branches(tmp_path, response_factory):
    pet = json.dumps({"type": "object", "properties": {"petType": {"type": "string"}}})
    (tmp_path / "cat.json").write_text(pet)
    (tmp_path / "bird.json").write_text(pet)
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.2",
                "info": {"title": "Test", "version": "1"},
                "paths": {
                    "/pets": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "anyOf": [
                                                    {"$ref": "cat.json"},
                                                    {"$ref": "bird.json#"},
                                                    {"$ref": "#/components/schemas/Dog"},
                                                ],
                                                "discriminator": {"propertyName": "petType"},
                                            }
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
                "components": {"schemas": {"Dog": {"type": "object", "properties": {"petType": {"type": "string"}}}}},
            }
        )
    )
    operation = schemathesis.openapi.from_path(str(schema_path))["/pets"]["GET"]
    response = Response.from_requests(response_factory.requests(content=b'{"petType": "Dog"}'), True)
    assert operation.validate_response(response) is None


_USER_PROFILE_SCHEMA = {
    "/users": {
        "post": {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}
                },
            },
            "responses": {
                "201": {"content": {"application/json": {"schema": {"type": "object"}}}},
            },
        },
    },
    "/users/{userId}": {
        "delete": {
            "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "string"}}],
            "responses": {"204": {"description": "Deleted"}, "500": {"description": "Server error"}},
        },
    },
    "/users/{userId}/profile": {
        "get": {
            "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
        },
    },
}


def _build_user_profile_chain(ctx, response_factory, *, delete_status: int, get_headers=None):
    schema = ctx.openapi.load_schema(_USER_PROFILE_SCHEMA)
    post_operation = schema["/users"]["POST"]
    delete_operation = schema["/users/{userId}"]["DELETE"]
    get_operation = schema["/users/{userId}/profile"]["GET"]

    post_case = post_operation.Case(body={"name": "alice"})
    delete_case = delete_operation.Case(path_parameters={"userId": "alice"})
    get_case = get_operation.Case(path_parameters={"userId": "alice"})

    post_response = Response.from_requests(response_factory.requests(status_code=201), True)
    delete_response = Response.from_requests(response_factory.requests(status_code=delete_status), True)
    get_response = Response.from_requests(response_factory.requests(status_code=200, headers=get_headers), True)

    recorder = ScenarioRecorder(label="use-after-free-test")
    recorder.record_case(parent_id=None, case=post_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=post_case.id, response=post_response)
    recorder.record_case(parent_id=post_case.id, case=delete_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=delete_case.id, response=delete_response)
    recorder.record_case(parent_id=delete_case.id, case=get_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=get_case.id, response=get_response)

    return check_context(recorder=recorder), get_case, get_response


@pytest.mark.parametrize("delete_status", [500, 404], ids=["server-crash", "not-found"])
def test_use_after_free_skips_when_delete_failed(ctx, response_factory, delete_status):
    # When DELETE returns 5xx (server crash) or 404 (nothing to free), the resource was never
    # actually deleted, so a subsequent 2xx read is not a use-after-free.
    context, get_case, get_response = _build_user_profile_chain(ctx, response_factory, delete_status=delete_status)
    assert use_after_free(context, get_response, get_case) is None


def test_use_after_free_fires_when_delete_succeeded(ctx, response_factory):
    context, get_case, get_response = _build_user_profile_chain(ctx, response_factory, delete_status=204)
    with pytest.raises(UseAfterFree):
        use_after_free(context, get_response, get_case)


@pytest.mark.parametrize(
    ("headers", "hint"),
    [
        ({"Age": "75430"}, "\n\nThe response came from a cache (`Age: 75430`) and may be stale"),
        ({"X-Cache": "MISS, HIT"}, "\n\nThe response came from a cache (`X-Cache: MISS, HIT`) and may be stale"),
        ({"Age": "1"}, "\n\nThe response came from a cache (`Age: 1`) and may be stale"),
        ({"Age": "0", "X-Cache": "MISS"}, ""),
        ({"Age": "\u00b2"}, ""),
        (None, ""),
    ],
    ids=["age", "x-cache-hit", "one-second-age", "fresh", "non-ascii-digit-age", "no-cache-headers"],
)
def test_use_after_free_hints_at_cached_response(ctx, response_factory, headers, hint):
    context, get_case, get_response = _build_user_profile_chain(
        ctx, response_factory, delete_status=204, get_headers=headers
    )
    with pytest.raises(UseAfterFree) as exc_info:
        use_after_free(context, get_response, get_case)
    assert exc_info.value.message == (
        "The API did not return a `HTTP 404 Not Found` response (got `HTTP 200 OK`) for a resource that was "
        f"previously deleted.\n\nThe resource was deleted with `DELETE /users/alice`{hint}"
    )


_NESTED_RESOURCE_SCHEMA = {
    "/foos": {
        "get": {"responses": {"200": {"description": "OK"}}},
    },
    "/foos/{fooId}/bars/{barId}": {
        "delete": {
            "parameters": [
                {"in": "path", "name": "fooId", "required": True, "schema": {"type": "string"}},
                {"in": "path", "name": "barId", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {"204": {"description": "Deleted"}},
        },
        "put": {
            "parameters": [
                {"in": "path", "name": "fooId", "required": True, "schema": {"type": "string"}},
                {"in": "path", "name": "barId", "required": True, "schema": {"type": "string"}},
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
            "responses": {"201": {"description": "Created"}, "200": {"description": "Replaced"}},
        },
    },
}


@pytest.mark.parametrize("put_status", [201, 200], ids=["created", "replaced"])
def test_use_after_free_skips_recreation_via_put(ctx, response_factory, put_status):
    # PUT re-creates a resource at the target URI, so a success after DELETE is a re-creation, not a use-after-free.
    schema = ctx.openapi.load_schema(_NESTED_RESOURCE_SCHEMA)
    get_case = schema["/foos"]["GET"].Case()
    delete_case = schema["/foos/{fooId}/bars/{barId}"]["DELETE"].Case(path_parameters={"fooId": "1", "barId": "2"})
    put_case = schema["/foos/{fooId}/bars/{barId}"]["PUT"].Case(path_parameters={"fooId": "1", "barId": "2"}, body={})

    get_response = Response.from_requests(response_factory.requests(status_code=200), True)
    delete_response = Response.from_requests(response_factory.requests(status_code=204), True)
    put_response = Response.from_requests(response_factory.requests(status_code=put_status), True)

    recorder = ScenarioRecorder(label="use-after-free-put")
    recorder.record_case(parent_id=None, case=get_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=get_case.id, response=get_response)
    recorder.record_case(parent_id=get_case.id, case=delete_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=delete_case.id, response=delete_response)
    recorder.record_case(parent_id=delete_case.id, case=put_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=put_case.id, response=put_response)

    assert use_after_free(check_context(recorder=recorder), put_response, put_case) is None


_RECREATION_SCHEMA = {
    **_USER_PROFILE_SCHEMA,
    "/users/{userId}": {
        **_USER_PROFILE_SCHEMA["/users/{userId}"],
        "put": {
            "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "Replaced"}},
        },
    },
    "/orders": {"post": {"responses": {"201": {"description": "Created"}}}},
}


@pytest.mark.parametrize(
    ("label", "path_parameters", "status_code", "order", "raises"),
    [
        ("POST /users", None, 201, ("other", "read"), False),
        ("POST /users", None, 200, ("other", "read"), False),
        ("PUT /users/{userId}", {"userId": "alice"}, 200, ("other", "read"), False),
        ("POST /users", None, 300, ("other", "read"), True),
        ("POST /users", None, 400, ("other", "read"), True),
        ("POST /users", None, None, ("other", "read"), True),
        ("POST /users", None, 201, ("read", "other"), True),
        ("DELETE /users/{userId}", {"userId": "alice"}, 204, ("other", "read"), True),
        ("POST /orders", None, 201, ("other", "read"), True),
    ],
    ids=[
        "created",
        "created-200",
        "replaced-at-own-path",
        "redirected",
        "creation-rejected",
        "no-response",
        "after-read",
        "delete",
        "unrelated-path",
    ],
)
def test_use_after_free_skips_resource_recreated_between_delete_and_read(
    ctx, response_factory, label, path_parameters, status_code, order, raises
):
    schema = ctx.openapi.load_schema(_RECREATION_SCHEMA)
    delete_case = schema["/users/{userId}"]["DELETE"].Case(path_parameters={"userId": "alice"})
    get_case = schema["/users/{userId}/profile"]["GET"].Case(path_parameters={"userId": "alice"})
    method, path = label.split(" ")
    other_case = schema[path][method].Case(path_parameters=path_parameters)
    get_response = Response.from_requests(response_factory.requests(status_code=200), True)

    recorder = ScenarioRecorder(label="use-after-free-recreation")
    post_case = schema["/users"]["POST"].Case(body={"name": "alice"})
    recorder.record_case(parent_id=None, case=post_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=post_case.id, response=response_factory.core(status_code=201))
    recorder.record_case(parent_id=post_case.id, case=delete_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=delete_case.id, response=response_factory.core(status_code=204))
    other_response = response_factory.core(status_code=status_code) if status_code is not None else None
    steps = {"other": (None, other_case, other_response), "read": (delete_case.id, get_case, get_response)}
    for parent_id, case, response in (steps[name] for name in order):
        recorder.record_case(parent_id=parent_id, case=case, transition=None, is_transition_applied=False)
        if response is not None:
            recorder.record_response(case_id=case.id, response=response)

    if raises:
        with pytest.raises(UseAfterFree):
            use_after_free(check_context(recorder=recorder), get_response, get_case)
    else:
        assert use_after_free(check_context(recorder=recorder), get_response, get_case) is None


@pytest.mark.parametrize(
    ("path", "path_parameters", "status_code", "should_raise"),
    [
        ("/items/{item_id}", {"item_id": "42"}, 404, False),
        ("/items/{item_id}", {"item_id": "42"}, 200, True),
        ("/items", None, 404, True),
    ],
)
def test_unsupported_method_404_on_templated_path(
    ctx, response_factory, path, path_parameters, status_code, should_raise
):
    # A generated path parameter rarely points at an existing resource, so routing 404s before method dispatch.
    parameters = (
        [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}] if path_parameters else []
    )
    schema = ctx.openapi.load_schema(
        {path: {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}}
    )
    operation = schema[path]["GET"]
    case = operation.Case(
        path_parameters=path_parameters,
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.COVERAGE,
                data=CoveragePhaseData(
                    scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD,
                    description="Unspecified HTTP method: POST",
                    location=None,
                    parameter=None,
                    parameter_location=None,
                ),
            ),
        ),
    )
    context = check_context()
    response = response_factory.requests(status_code=status_code)
    if should_raise:
        with pytest.raises(Failure):
            unsupported_method(context, response, case)
    else:
        assert unsupported_method(context, response, case) is None


@pytest.mark.parametrize(
    "scenario", [CoverageScenario.MALFORMED_CONTENT_TYPE, CoverageScenario.UNSUPPORTED_CONTENT_TYPE]
)
def test_content_type_probes_skip_response_conformance(ctx, response_factory, scenario):
    operation = ctx.openapi.load_operation(
        "GET /items", {"responses": {"200": {"description": "OK", "content": {"application/json": {}}}}}
    )
    case = operation.Case(
        headers={"Content-Type": "multipart/form-data"},
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
            components={},
            phase=PhaseInfo.coverage(scenario=scenario, description="Content-Type probe"),
        ),
    )

    assert (
        content_type_conformance(check_context(), response_factory.requests(status_code=200, content_type=None), case)
        is True
    )


@pytest.mark.parametrize(
    ("pinned", "should_raise"),
    [(True, True), (False, False)],
    ids=["pinned", "generated"],
)
def test_unsupported_method_404_on_pinned_templated_path(ctx, response_factory, pinned, should_raise):
    # A pinned path parameter names a resource the user vouched for, so 404 is a finding rather than a miss.
    operation = ctx.openapi.load_operation(
        "GET /items/{item_id}",
        {
            "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        path_parameters={"item_id": "42"},
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.COVERAGE,
                data=CoveragePhaseData(
                    scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD,
                    description="Unspecified HTTP method: TRACE",
                    location=None,
                    parameter=None,
                    parameter_location=None,
                ),
            ),
        ),
    )
    override = (
        Override(query={}, headers={}, cookies={}, path_parameters={"item_id": "42"}, body={}) if pinned else None
    )
    context = check_context(override=override)
    response = response_factory.requests(status_code=404, method="TRACE")
    if should_raise:
        with pytest.raises(UnsupportedMethodResponse, match=re.escape("Unsupported method TRACE returned 404")):
            unsupported_method(context, response, case)
    else:
        assert unsupported_method(context, response, case) is None


@pytest.mark.parametrize(
    ("pinned", "should_raise"),
    [(True, True), (False, False)],
    ids=["pinned", "generated"],
)
def test_missing_required_header_404_on_templated_path(ctx, response_factory, pinned, should_raise):
    # A drawn identifier rarely exists, so the server can 404 before it ever looks at headers.
    operation = ctx.openapi.load_operation(
        "DELETE /items/{item_id}",
        {
            "parameters": [
                {"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}},
                {"name": "X-Token", "in": "header", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {"204": {"description": "Deleted"}},
        },
    )
    case = operation.Case(
        path_parameters={"item_id": "42"},
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.COVERAGE,
                data=CoveragePhaseData(
                    scenario=CoverageScenario.MISSING_PARAMETER,
                    description="Missing `X-Token` at header",
                    location=None,
                    parameter="X-Token",
                    parameter_location=ParameterLocation.HEADER,
                ),
            ),
        ),
    )
    override = (
        Override(query={}, headers={}, cookies={}, path_parameters={"item_id": "42"}, body={}) if pinned else None
    )
    context = check_context(override=override)
    response = response_factory.requests(status_code=404, method="DELETE")
    if should_raise:
        with pytest.raises(MissingHeaderNotRejected, match=re.escape("Got 404 when missing required 'X-Token' header")):
            missing_required_header(context, response, case)
    else:
        assert missing_required_header(context, response, case) is None


@pytest.mark.parametrize(
    ("secured", "status_code", "expected_message"),
    [
        (True, 401, None),
        (True, 403, None),
        (False, 401, "Unsupported method TRACE returned 401"),
        (False, 403, "Unsupported method TRACE returned 403"),
        (True, 500, "Unsupported method TRACE returned 500"),
        (True, 405, "TRACE returned 405 without required `Allow` header"),
        (True, 429, None),
        (False, 429, None),
    ],
    ids=[
        "secured-401",
        "secured-403",
        "open-401",
        "open-403",
        "secured-non-auth-status",
        "secured-405-without-allow",
        "secured-429",
        "open-429",
    ],
)
def test_unsupported_method_auth_before_routing(ctx, response_factory, secured, status_code, expected_message):
    # Many frameworks authenticate and rate-limit before method dispatch, so 401/403/429 can precede 405.
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            **({"security": [{"basicAuth": []}]} if secured else {}),
            "responses": {"200": {"description": "OK"}},
        },
        components={"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
    )
    case = operation.Case(
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.NEGATIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.COVERAGE,
                data=CoveragePhaseData(
                    scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD,
                    description="Unspecified HTTP method: TRACE",
                    location=None,
                    parameter=None,
                    parameter_location=None,
                ),
            ),
        ),
    )
    response = response_factory.requests(status_code=status_code, method="TRACE")
    if expected_message is None:
        assert unsupported_method(check_context(), response, case) is None
    else:
        with pytest.raises(UnsupportedMethodResponse, match=re.escape(expected_message)):
            unsupported_method(check_context(), response, case)


def _token_endpoint_paths():
    return {
        "/token": {"post": {"responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}}}},
        "/items": {"post": {"responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}}}},
    }


@pytest.mark.parametrize(
    ("token_url", "path", "status_code", "should_raise"),
    [
        ("/token", "/token", 400, False),
        ("/token", "/token", 422, False),
        ("https://example.com/token", "/token", 400, False),
        ("token", "/token", 400, False),
        ("/token", "/items", 400, True),
        ("/token", "/token", 405, True),
        ("/other", "/token", 400, True),
    ],
    ids=[
        "token-endpoint-400",
        "token-endpoint-422",
        "absolute-token-url",
        "relative-token-url-without-slash",
        "other-operation-still-fails",
        "unrelated-status-still-fails",
        "token-url-elsewhere",
    ],
)
def test_positive_data_acceptance_token_endpoint(ctx, response_factory, token_url, path, status_code, should_raise):
    schema = ctx.openapi.load_schema(
        _token_endpoint_paths(),
        components={
            "securitySchemes": {
                "oauth2": {"type": "oauth2", "flows": {"password": {"tokenUrl": token_url, "scopes": {}}}}
            }
        },
    )
    case = schema[path]["POST"].Case(_meta=build_metadata())
    response = response_factory.requests(status_code=status_code)

    if should_raise:
        with pytest.raises(Failure):
            positive_data_acceptance(check_context(), response, case)
    else:
        assert positive_data_acceptance(check_context(), response, case) is None


@pytest.mark.parametrize(
    "components",
    [
        {},
        {"securitySchemes": {"oauth2": {"type": "oauth2", "flows": {"password": {"tokenUrl": "", "scopes": {}}}}}},
    ],
    ids=["no-schemes", "after-empty-token-url"],
)
def test_positive_data_acceptance_configured_dynamic_auth_path(ctx, response_factory, components):
    schema = ctx.openapi.load_schema(_token_endpoint_paths(), components=components)
    schema.config.auth.dynamic.schemes["oauth2"] = DynamicTokenAuthConfig(
        path="/token", extract_from="body", extract_selector="/access_token"
    )
    response = response_factory.requests(status_code=400)

    granting = schema["/token"]["POST"].Case(_meta=build_metadata())
    assert positive_data_acceptance(check_context(), response, granting) is None

    other = schema["/items"]["POST"].Case(_meta=build_metadata())
    with pytest.raises(Failure):
        positive_data_acceptance(check_context(), response, other)


def test_positive_data_acceptance_token_url_carries_base_path(ctx, response_factory):
    # Swagger 2.0 states the token URL with `basePath`, which operation paths do not carry.
    operation = ctx.openapi.load_operation(
        "POST /token",
        {"responses": {"200": {"description": "OK"}}},
        version="2.0",
        basePath="/api/v1",
        securityDefinitions={
            "oauth2": {"type": "oauth2", "flow": "password", "tokenUrl": "/api/v1/token", "scopes": {}}
        },
    )
    case = operation.Case(_meta=build_metadata())
    assert positive_data_acceptance(check_context(), response_factory.requests(status_code=400), case) is None


def test_positive_data_acceptance_empty_token_path_does_not_match_root(ctx, response_factory):
    schema = ctx.openapi.load_schema({"/": {"post": {"responses": {"200": {"description": "OK"}}}}})
    schema.config.auth.dynamic.schemes["oauth2"] = DynamicTokenAuthConfig(extract_from="body")
    case = schema["/"]["POST"].Case(_meta=build_metadata())
    with pytest.raises(Failure):
        positive_data_acceptance(check_context(), response_factory.requests(status_code=400), case)


ALLOW_SCHEMA_PATHS = {
    "/items": {
        "parameters": [{"name": "trace_id", "in": "header", "schema": {"type": "string"}}],
        "get": {"responses": {"200": {"description": "OK"}}},
        "post": {"responses": {"201": {"description": "Created"}}},
    }
}


@pytest.mark.parametrize(
    ("headers", "method", "expected"),
    [
        ({"Allow": "GET, POST, HEAD, OPTIONS"}, "OPTIONS", None),
        ({"Allow": "get,post"}, "OPTIONS", None),
        ({"Allow": "GET ,   POST"}, "OPTIONS", None),
        (
            {"Allow": "GET, HEAD, OPTIONS"},
            "OPTIONS",
            "`Allow: GET, HEAD, OPTIONS` does not match the schema. Missing documented methods: POST",
        ),
        (
            {"Allow": "GET, POST, DELETE"},
            "OPTIONS",
            "`Allow: GET, POST, DELETE` does not match the schema. Undocumented methods advertised: DELETE",
        ),
        (
            {"Allow": "GET, DELETE"},
            "OPTIONS",
            "Missing documented methods: POST. Undocumented methods advertised: DELETE",
        ),
        ({}, "OPTIONS", None),
        ({"Allow": ""}, "OPTIONS", None),
        ({"Allow": "GET"}, "GET", None),
    ],
    ids=[
        "match",
        "case-insensitive",
        "whitespace",
        "missing-method",
        "undocumented-method",
        "both",
        "no-allow-header",
        "empty-allow-header",
        "not-an-options-request",
    ],
)
def test_allow_header_conformance(ctx, response_factory, headers, method, expected):
    schema = ctx.openapi.load_schema(ALLOW_SCHEMA_PATHS)
    case = schema["/items"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(headers=headers, method=method), verify=True)
    context = check_context()
    if expected is None:
        assert allow_header_conformance(context, response, case) is None
    else:
        with pytest.raises(AllowHeaderMismatch, match=re.escape(expected)):
            allow_header_conformance(context, response, case)


def test_allow_header_conformance_repeated_header(ctx, response_factory):
    raw = response_factory.requests(method="OPTIONS")
    raw.raw.headers.add("Allow", "GET")
    raw.raw.headers.add("Allow", "POST")
    case = ctx.openapi.load_schema(ALLOW_SCHEMA_PATHS)["/items"]["GET"].Case()
    assert allow_header_conformance(check_context(), Response.from_requests(raw, verify=True), case) is None


def _negative_case(operation, location, parameter, mutation=None, phase=TestPhase.FUZZING, **kwargs):
    return operation.Case(
        _meta=build_metadata(
            generation_modes=[GenerationMode.NEGATIVE],
            parameter=parameter,
            parameter_location=location,
            mutations=(mutation,) if mutation else (),
            description="Invalid component",
            phase=phase,
            **{location.container_name: GenerationMode.NEGATIVE},
        ),
        **kwargs,
    )


PAGE_SIZE = {"type": "integer", "minimum": 1, "maximum": 100}


@pytest.mark.parametrize(
    ("location", "parameter_schema", "value", "type_mutation", "reported"),
    [
        # Repeated keys let frameworks such as Django pick the valid `1` (GH-3697).
        pytest.param(
            ParameterLocation.QUERY, PAGE_SIZE, [True, 1], True, False, id="multi_element_array_with_valid_element"
        ),
        # `"44"` reaches the wire as `44`, which Django parses as an integer (GH-3931).
        pytest.param(
            ParameterLocation.QUERY,
            PAGE_SIZE,
            [-1.2097890770124232e65, {"a": None}, [[-8.080921524865554e-19], "x"], [], "44"],
            True,
            False,
            id="multi_element_array_string_numeric_element",
        ),
        # Object keys serialize as values, so `{"5": "x"}` sends `?value=5`.
        pytest.param(
            ParameterLocation.QUERY,
            {"type": "integer"},
            {"5": "x"},
            True,
            False,
            id="query_object_mutation_with_numeric_key",
        ),
        # Encoded `+1` decodes back to an integer-like value accepted by many servers.
        pytest.param(
            ParameterLocation.PATH, {"type": "integer"}, "%2B1", True, False, id="path_string_numeric_serialization"
        ),
        # Already-encoded path values are judged by the string they carry.
        pytest.param(
            ParameterLocation.PATH,
            {"type": "string", "minLength": 1},
            EncodedPath("abc"),
            True,
            False,
            id="encoded_path_value",
        ),
        pytest.param(
            ParameterLocation.PATH,
            {"type": "string", "minLength": 2},
            "ab",
            False,
            False,
            id="negative_path_value_that_matches_schema",
        ),
        pytest.param(
            ParameterLocation.PATH, {"type": "number"}, "abc", True, True, id="path_non_numeric_string_for_number"
        ),
    ],
)
@pytest.mark.parametrize("phase", [TestPhase.FUZZING, TestPhase.STATEFUL], ids=["fuzzing", "stateful"])
def test_negative_data_rejection_single_parameter(
    ctx, response_factory, location, parameter_schema, value, type_mutation, reported, phase
):
    path = "/items/{value}" if location == ParameterLocation.PATH else "/items"
    parameter = {
        "name": "value",
        "in": location.value,
        "required": location == ParameterLocation.PATH,
        "schema": parameter_schema,
    }
    operation = ctx.openapi.load_schema(
        {path: {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}}}
    )[path]["GET"]
    mutation = (
        _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="value", location=location) if type_mutation else None
    )
    case = _negative_case(
        operation, location, "value", mutation, phase=phase, **{location.container_name: {"value": value}}
    )
    response = response_factory.requests(status_code=200)
    _assert_rejection(case, response, reported=reported)


# Validity can't be decided when the validator rejects the schema itself, so nothing is reported.
@pytest.mark.parametrize(
    ("location", "parameter_schema", "value", "mutation"),
    [
        pytest.param(
            ParameterLocation.QUERY,
            # A literal in Python regex, but an invalid ECMA 262 pattern
            {"type": "string", "pattern": "{,3}"},
            ["a", "b"],
            None,
            id="query-array-invalid-pattern",
        ),
        pytest.param(
            ParameterLocation.PATH,
            {"type": "array", "items": {"type": "string", "pattern": "{,3}"}},
            "a,b",
            None,
            id="path-array-invalid-pattern",
        ),
        pytest.param(
            ParameterLocation.PATH,
            {"type": "integer", "multipleOf": 0},
            "abc",
            OperatorKind.CHANGE_TYPE,
            id="path-type-mutation-zero-multiple-of",
        ),
        pytest.param(
            ParameterLocation.QUERY,
            {"type": "integer", "multipleOf": 0},
            "abc",
            None,
            id="query-zero-multiple-of",
        ),
    ],
)
def test_negative_data_rejection_skips_schemas_rejected_by_validator(
    ctx, response_factory, location, parameter_schema, value, mutation
):
    parameters = [{"name": "key", "in": location.value, "required": True, "schema": parameter_schema}]
    if location != ParameterLocation.PATH:
        parameters.append({"name": "key", "in": "path", "required": True, "schema": {"type": "string"}})
    operation = ctx.openapi.load_operation(
        "GET /items/{key}", {"parameters": parameters, "responses": {"200": {"description": "OK"}}}
    )
    case = _negative_case(
        operation,
        location,
        "key",
        _mutation(mutation, ("type",), parameter="key", location=location) if mutation else None,
        **{location.container_name: {"key": value}},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(status_code=200), case) is None


@pytest.mark.parametrize("query_value", ["abc", ["abc"]], ids=["scalar", "array"])
def test_negative_data_rejection_reports_header_beside_query_rejected_by_validator(ctx, response_factory, query_value):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "q", "in": "query", "required": True, "schema": {"type": "integer", "multipleOf": 0}},
                {"name": "X-Id", "in": "header", "required": True, "schema": {"type": "integer"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid component",
            mutations=(
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="q"),
                _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="X-Id", location=ParameterLocation.HEADER),
            ),
        ),
        query={"q": query_value},
        headers={"X-Id": "abc"},
    )
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(status_code=200), case)


_NAME_PROPERTY = {"type": "object", "properties": {"name": {"type": "string"}}}


@pytest.mark.parametrize(
    ("version", "content", "components", "body", "media_type", "hint"),
    [
        pytest.param(
            "3.0.2",
            {"application/xml": {"schema": {"type": "object"}}, "application/json": {"schema": _NAME_PROPERTY}},
            {},
            {"name": "x", "extra": "yes"},
            "application/json",
            _EXTRA_PROPERTY_HINT,
            id="matching-media-type-after-another",
        ),
        pytest.param(
            "3.0.2",
            {"application/json; charset=utf-8": {"schema": _NAME_PROPERTY}},
            {},
            {"name": "x", "extra": "yes"},
            "application/json",
            _EXTRA_PROPERTY_HINT,
            id="declared-with-parameters",
        ),
        pytest.param(
            "3.1.0",
            {"application/json": {"schema": {"allOf": [True, _NAME_PROPERTY]}}},
            {},
            {"name": "x", "extra": "yes"},
            "application/json",
            _EXTRA_PROPERTY_HINT,
            id="boolean-branch",
        ),
        pytest.param(
            "3.0.2",
            {"application/json": {"schema": {**_NAME_PROPERTY, "additionalProperties": {"type": "string"}}}},
            {},
            {"name": "x", "extra": "yes"},
            "application/json",
            _EXTRA_PROPERTY_HINT,
            id="additional-properties-schema",
        ),
        pytest.param(
            "3.0.2",
            {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
            {
                "schemas": {
                    "Node": {
                        "allOf": [{"$ref": "#/components/schemas/Node"}],
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
            {"name": "x", "extra": "yes"},
            "application/json",
            _EXTRA_PROPERTY_HINT,
            id="recursive-ref",
        ),
        pytest.param(
            "3.1.0",
            {"application/json": {"schema": True}},
            {},
            {"name": "x", "extra": "yes"},
            "application/json",
            "",
            id="boolean-schema",
        ),
        pytest.param(
            "3.0.2",
            {"application/json": {"schema": _NAME_PROPERTY}},
            {},
            {"name": 1, "extra": "yes"},
            "application/json",
            "",
            id="declared-properties-invalid",
        ),
        pytest.param(
            "3.0.2",
            {"application/json": {"schema": _NAME_PROPERTY}},
            {},
            {"name": "x", "extra": "yes"},
            "application/x-other",
            "",
            id="undeclared-media-type",
        ),
    ],
)
def test_positive_data_acceptance_additional_properties_hint(
    ctx, response_factory, version, content, components, body, media_type, hint
):
    operation = ctx.openapi.load_operation(
        "POST /foo",
        {
            "requestBody": {"required": True, "content": content},
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
        components=components,
    )
    case = operation.Case(body=body, media_type=media_type, _meta=build_metadata())
    with pytest.raises(RejectedPositiveData) as exc:
        positive_data_acceptance(check_context(), _opaque_rejection(response_factory), case)
    assert (
        exc.value.message
        == f"Valid data should have been accepted\nExpected: 2xx, 3xx, 401, 403, 404, 409, 429, 5xx{hint}"
    )


def test_additional_properties_hint_ignores_blame_on_query_parameter(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "POST /foo",
        {
            "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": _NAME_PROPERTY}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(
        body={"name": "value", "extra": "yes"},
        query={"limit": 5},
        media_type="application/json",
        _meta=build_metadata(),
    )
    error_body = {"detail": [{"type": "missing", "loc": ["query", "limit"], "msg": "Field required"}]}
    response = Response.from_requests(
        response_factory.requests(status_code=400, content=json.dumps(error_body).encode()), verify=True
    )
    with pytest.raises(RejectedPositiveData) as exc:
        positive_data_acceptance(check_context(), response, case)
    assert exc.value.message == (
        f"Valid data should have been accepted\nExpected: 2xx, 3xx, 401, 403, 404, 409, 429, 5xx{_EXTRA_PROPERTY_HINT}"
    )


# An implicit flow has no token endpoint, so no operation is a credential grant.
def test_positive_data_acceptance_reports_rejection_on_implicit_oauth_flow_operation(ctx, response_factory):
    operation = ctx.openapi.load_operation(
        "POST /token",
        {"responses": {"200": {"description": "OK"}}},
        components={
            "securitySchemes": {
                "oauth": {
                    "type": "oauth2",
                    "flows": {"implicit": {"authorizationUrl": "/token", "scopes": {}}},
                }
            }
        },
    )
    case = operation.Case(_meta=build_metadata())
    with pytest.raises(RejectedPositiveData):
        positive_data_acceptance(check_context(), _opaque_rejection(response_factory), case)


ITEM = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}


def response_validator(schema, label, status="200", media_type="application/json", validate_formats=True):
    response = schema.find_operation_by_label(label).responses.get(status)
    resolved = response.get_schema(media_type)
    return response.get_validator(resolved.media_type, resolved.schema, validate_formats=validate_formats)


def json_ok(schema):
    return {"200": {"description": "OK", "content": {"application/json": {"schema": schema}}}}


def test_operations_sharing_a_response_schema_share_one_validator(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/a": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Item"})}},
            "/b": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Item"})}},
        },
        components={"schemas": {"Item": ITEM}},
    )

    assert response_validator(schema, "GET /a") is response_validator(schema, "GET /b")


def test_shared_response_reference_shares_one_validator(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/a": {"get": {"responses": {"200": {"$ref": "#/components/responses/Ok"}}}},
            "/b": {"get": {"responses": {"200": {"$ref": "#/components/responses/Ok"}}}},
        },
        components={
            "schemas": {"Item": ITEM},
            "responses": {
                "Ok": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}},
                }
            },
        },
    )

    assert response_validator(schema, "GET /a") is response_validator(schema, "GET /b")


def test_response_validator_survives_operation_rebuilds(ctx):
    schema = ctx.openapi.load_schema(
        {"/a": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Item"})}}},
        components={"schemas": {"Item": ITEM}},
    )

    first = response_validator(schema, "GET /a")
    # Each lookup builds a fresh operation; its response validator is not rebuilt with it.
    assert response_validator(schema, "GET /a") is first


def test_different_validate_formats_do_not_share_a_response_validator(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/a": {"get": {"responses": json_ok({"type": "string", "format": "date"})}},
            "/b": {"get": {"responses": json_ok({"type": "string", "format": "date"})}},
        }
    )

    strict = response_validator(schema, "GET /a", validate_formats=True)
    lenient = response_validator(schema, "GET /b", validate_formats=False)

    assert (strict.is_valid("not a date"), lenient.is_valid("not a date")) == (False, True)


def test_separately_loaded_schemas_do_not_share_response_validators(ctx):
    paths = {"/a": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Item"})}}}
    first = ctx.openapi.load_schema(paths, components={"schemas": {"Item": ITEM}})
    second = ctx.openapi.load_schema(paths, components={"schemas": {"Item": {"type": "string"}}})

    verdicts = (
        response_validator(first, "GET /a").is_valid({"id": 1}),
        response_validator(second, "GET /a").is_valid({"id": 1}),
    )

    assert verdicts == (True, False)


def test_same_response_schema_text_in_two_documents_does_not_share_a_validator(ctx):
    paths = {
        "/a": {"get": {"responses": {"200": {"$ref": "./first/responses.json#/Ok"}}}},
        "/b": {"get": {"responses": {"200": {"$ref": "./second/responses.json#/Ok"}}}},
    }
    schema_path = ctx.openapi.write_schema(paths)
    ok = {"description": "OK", "content": {"application/json": {"schema": {"$ref": "#/Item"}}}}
    ctx.makefile({"Ok": ok, "Item": ITEM}, filename="responses", parent="first")
    ctx.makefile({"Ok": ok, "Item": {"type": "string"}}, filename="responses", parent="second")
    schema = schemathesis.openapi.from_path(str(schema_path))

    verdicts = (
        response_validator(schema, "GET /a").is_valid({"id": 1}),
        response_validator(schema, "GET /b").is_valid({"id": 1}),
    )

    assert verdicts == (True, False)


def test_unresolvable_response_reference_is_reported_for_every_response(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/a": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Missing"})}},
            "/b": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Missing"})}},
        }
    )

    unresolvable = [
        schema.find_operation_by_label(label).responses.get("200").get_schema("application/json").unresolvable_reference
        for label in ("GET /a", "GET /b")
    ]

    assert unresolvable == ["#/components/schemas/Missing", "#/components/schemas/Missing"]


def test_validating_responses_leaves_the_shared_schema_unchanged(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/a": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Item"})}},
            "/b": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Item"})}},
        },
        components={"schemas": {"Item": ITEM}},
    )
    shared = schema.find_operation_by_label("GET /a").responses.get("200").get_schema("application/json").schema
    before = deepclone(shared)

    for label in ("GET /a", "GET /b"):
        case = schema.find_operation_by_label(label).Case()
        response = response_factory.requests(content=b'{"id": "not an integer"}')
        with pytest.raises(FailureGroup):
            case.validate_response(response)

    assert shared == before


def test_concurrent_first_use_builds_one_shared_response_validator(ctx):
    # Switching threads often makes the race between first users likely.
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    wide = {"type": "object", "properties": {f"field{i}": {"type": "string"} for i in range(2000)}}
    labels = [f"GET /op{i}" for i in range(16)]
    schema = ctx.openapi.load_schema(
        {f"/op{i}": {"get": {"responses": json_ok({"$ref": "#/components/schemas/Wide"})}} for i in range(16)},
        components={"schemas": {"Wide": wide}},
    )
    operations = [schema.find_operation_by_label(label) for label in labels]
    barrier = threading.Barrier(len(operations))
    validators = []

    def validate(operation):
        barrier.wait()
        response = operation.responses.get("200")
        resolved = response.get_schema("application/json")
        validators.append(response.get_validator(resolved.media_type, resolved.schema))

    threads = [threading.Thread(target=validate, args=(operation,)) for operation in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    sys.setswitchinterval(previous)

    assert len({id(validator) for validator in validators}) == 1


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
@pytest.mark.parametrize("format", ["uuid", "duration"])
def test_draft4_response_rejects_invalid_supplemental_format(ctx, response_factory, version, format):
    schema = {"type": "string", "format": format}
    if version == "2.0":
        responses = {"200": {"description": "OK", "schema": schema}}
    else:
        responses = json_ok(schema)
    operation = ctx.openapi.load_operation(
        "GET /a", {"produces": ["application/json"], "responses": responses}, version=version
    )
    response = response_factory.requests(content=b'"nope"')

    with pytest.raises(FailureGroup, match=f'is not a "{format}"'):
        operation.Case().validate_response(response, checks=[response_schema_conformance])
