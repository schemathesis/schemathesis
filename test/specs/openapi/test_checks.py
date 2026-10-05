import json
import re

import pytest
import requests
import yaml

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.config._auth import DynamicTokenAuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.core.failures import AcceptedNegativeData, Failure, MalformedJson
from schemathesis.core.mutations import OperatorKind
from schemathesis.core.parameters import EncodedPath, ParameterLocation
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
    TestPhase,
)
from schemathesis.generation.overrides import Override
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
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
from schemathesis.specs.openapi.negative.mutations import Mutation, MutationChannel, MutationMetadata
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
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description=description,
                parameter=parameter,
                parameter_location=parameter_location,
                location=location,
                mutations=mutations,
            ),
        ),
    )


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


def test_has_only_additional_properties_with_large_quantifier_pattern(ctx):
    # Patterns with large quantifiers require pattern_options with sufficient size_limit
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
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
                }
            }
        }
    )
    operation = schema["/test"]["POST"]
    case = operation.Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE),
        query={"key": "valid", "unknown": "extra"},
    )
    # Should not raise - the validator should handle patterns with large quantifiers
    assert has_only_additional_properties_in_non_body_parameters(case) is True


def _boolean_parameter_case(ctx, location, value):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {"in": location.value, "name": "flag", "required": True, "schema": {"type": "boolean"}},
                    ]
                }
            }
        }
    )
    return schema["/test"]["GET"].Case(
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


def _array_query_case(ctx, query, mutation):
    schema = ctx.openapi.load_schema(
        {
            "/tags": {
                "get": {
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
                }
            }
        }
    )
    return schema["/tags"]["GET"].Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            parameter=mutation.parameter,
            parameter_location=ParameterLocation.QUERY,
            mutations=(mutation,),
        ),
        query=query,
    )


def test_negative_data_rejection_reports_negated_array_query_parameter(ctx, response_factory):
    case = _array_query_case(ctx, {"tag": ["", ""]}, _mutation(OperatorKind.NEGATE_CONSTRAINTS, ("maxItems",), "tag"))
    assert has_only_additional_properties_in_non_body_parameters(case) is False
    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response_factory.requests(), case)


def test_negative_data_rejection_ignores_extras_next_to_array_query_parameter(ctx, response_factory):
    case = _array_query_case(ctx, {"tag": ["a"], "unknown": "junk"}, _ADDITIONAL_PROPERTIES_MUTATION)
    assert has_only_additional_properties_in_non_body_parameters(case) is True
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def _opaque_rejection(response_factory):
    # A rejection with nothing to attribute, so the hint falls back to its schema-side reasoning.
    return Response.from_requests(response_factory.requests(status_code=400), verify=True)


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
    "The server likely rejects unexpected fields. "
    "Add `additionalProperties: false` to your schema to prevent this."
)


@pytest.mark.parametrize(
    ("error_body", "hint"),
    [
        pytest.param(b'{"name": ["This field may not be blank."]}', "", id="declared-field-blamed"),
        pytest.param(b'{"extra": ["This field may not be blank."]}', _EXTRA_PROPERTY_HINT, id="extra-property-blamed"),
        pytest.param(b'{"detail": "Bad request"}', _EXTRA_PROPERTY_HINT, id="no-field-blamed"),
    ],
)
def test_additional_properties_hint_follows_response_attribution(ctx, response_factory, error_body, hint):
    # Blaming extras for a rejection the server pinned on a declared field points the reader at the wrong fix.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/foo"]["POST"].Case(
        body={"name": "value", "extra": "yes"}, media_type="application/json", _meta=build_metadata()
    )
    response = Response.from_requests(response_factory.requests(status_code=400, content=error_body), verify=True)
    with pytest.raises(RejectedPositiveData) as exc:
        positive_data_acceptance(check_context(), response, case)
    assert (
        exc.value.message == f"Valid data should have been accepted\nExpected: 2xx, 401, 403, 404, 409, 429, 5xx{hint}"
    )


@pytest.mark.parametrize(
    ("status_code", "should_raise"),
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
    response_factory, sample_schema, status_code, should_raise
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
    ctx = check_context()
    if should_raise:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(ctx, response, case)
    else:
        assert negative_data_rejection(ctx, response, case) is None


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


@pytest.mark.parametrize(("value", "is_accepted"), [("-1", True), ("5", False)], ids=["below-minimum", "valid"])
def test_negative_data_rejection_validates_numeric_wire_value_against_query_schema(
    response_factory, path_and_query_schema, value, is_accepted
):
    case = _path_and_query_case(path_and_query_schema, {"id": 7}, {"key": value}, {"X-Key": 7}, (_QUERY_TYPE_MUTATION,))
    if is_accepted:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response_factory.requests(), case)
    else:
        assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


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
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [{"in": "query", "name": "value", "required": False, **parameter}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"value": value},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    assert negative_data_rejection(check_context(), response, case) is None


def test_negative_data_rejection_validates_repeated_query_with_allowed_empty_value(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
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
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
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
    ("parameter", "value", "query", "accepted"),
    [
        ({"schema": {"type": "string"}}, {"first": [], "second": None}, "title=first&title=second", True),
        ({"schema": {"type": "string", "maxLength": 3}}, {"long": [], "ok": None}, "title=long&title=ok", True),
        ({"schema": {"enum": ["ok"]}}, {"bad": [], "worse": None}, "title=bad&title=worse", False),
        ({"schema": {"enum": ["ok"]}}, {"bad": [], "ok": None}, "title=bad&title=ok", True),
        (
            {"schema": {"enum": ["asc"]}, "allowEmptyValue": True},
            ["null", {"key": [], "": None}],
            "title=null&title=key&title=",
            True,
        ),
        ({"schema": {"enum": ["asc"]}}, ["null", {"key": [], "": None}], "title=null&title=key&title=", False),
        ({"schema": {"type": "string"}}, [[{"key": "value"}]], "title=%7B%27key%27%3A+%27value%27%7D", True),
        ({"schema": {"enum": ["asc"]}}, [[{"key": "value"}]], "title=%7B%27key%27%3A+%27value%27%7D", False),
        (
            {"schema": {"type": "string", "maxLength": 200}, "allowEmptyValue": True},
            [[{"key": "value"}]],
            "title=%7B%27key%27%3A+%27value%27%7D",
            True,
        ),
        (
            {"schema": {"type": "array", "items": {"enum": ["ok"]}}},
            {"bad": [], "ok": None},
            "title=bad&title=ok",
            False,
        ),
        *(
            ({"schema": {"type": "number", "maximum": 10}}, ["bad", {text: None}], f"title=bad&title={text}", True)
            for text in ("nan", "inf", "-Infinity", "1e400")
        ),
        ({"schema": {"type": "boolean"}}, ["abc", "yes"], "title=abc&title=yes", True),
        ({"schema": {"type": "boolean"}}, ["abc", {"On": None}], "title=abc&title=On", True),
        ({"schema": {"type": "boolean"}}, ["abc", {"enabled": None}], "title=abc&title=enabled", False),
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
    ctx, response_factory, parameter, value, query, accepted
):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [{"in": "query", "name": "title", **parameter}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
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
    if accepted:
        assert negative_data_rejection(check_context(), response, case) is None
    else:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)


@pytest.mark.parametrize(
    ("license_required", "license", "collection", "path", "url", "accepted"),
    [
        (False, [], "", "/audio", "http://127.0.0.1/audio?collection=", True),
        (False, None, "", "/audio", "http://127.0.0.1/audio?collection=", True),
        (True, [], "", "/audio", "http://127.0.0.1/audio?collection=", False),
        (False, [], "bogus", "/audio", "http://127.0.0.1/audio?collection=bogus", False),
        (False, [], "", "/other", "http://127.0.0.1/other?collection=", False),
        (False, [], ["bogus", ""], "/audio", "http://127.0.0.1/audio?collection=bogus&collection=", True),
        (False, [], {"tag": None}, "/audio", "http://127.0.0.1/audio?collection=tag", True),
        (False, None, ["bogus", "worse"], "/audio", "http://127.0.0.1/audio?collection=bogus&collection=worse", False),
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
    ctx, response_factory, license_required, license, collection, path, url, accepted
):
    schema = ctx.openapi.load_schema(
        {
            "/audio": {
                "get": {
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
                }
            }
        }
    )
    case = schema["/audio"]["GET"].Case(
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
    if accepted:
        assert negative_data_rejection(check_context(), response, case) is None
    else:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)


@pytest.mark.parametrize(
    ("authority", "accepted"),
    [("0", True), (0, True), ("yes", True), ("enabled", False)],
    ids=["zero-text", "zero-integer", "yes", "unknown-spelling"],
)
def test_negative_data_rejection_validates_boolean_spelling_beside_another_mutation(
    ctx, response_factory, authority, accepted
):
    schema = ctx.openapi.load_schema(
        {
            "/audio": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "title", "schema": {"type": "string", "minLength": 1}},
                        {"in": "query", "name": "authority", "schema": {"type": "boolean", "default": False}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/audio"]["GET"].Case(
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

    if accepted:
        assert negative_data_rejection(check_context(), response, case) is None
    else:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)


@pytest.mark.parametrize("location", [ParameterLocation.HEADER, ParameterLocation.COOKIE], ids=["header", "cookie"])
@pytest.mark.parametrize(
    ("value", "accepted"),
    [("yes", True), ("0", True), ("True", True), ("enabled", False)],
    ids=["yes", "zero", "capitalized", "unknown-spelling"],
)
def test_negative_data_rejection_validates_boolean_spelling_in_headers_and_cookies(
    ctx, response_factory, location, value, accepted
):
    operation = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"in": location.value, "name": "flag", "required": True, "schema": {"type": "boolean"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )["/items"]["GET"]
    case = _negative_case(operation, location, "flag", None, **{location.container_name: {"flag": value}})
    if accepted:
        assert negative_data_rejection(check_context(), response_factory.requests(), case) is None
    else:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response_factory.requests(), case)


def test_negative_data_rejection_reports_object_query_with_another_invalid_parameter(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "title", "schema": {"type": "string"}},
                        {"in": "header", "name": "X-Mode", "schema": {"enum": ["fast"]}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
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
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "value", "schema": {"type": "string"}},
                        {"in": "query", "name": "extra", "schema": extra_schema},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
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
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "value",
                            "required": required,
                            "schema": parameter_schema,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"value": value},
    )
    response = response_factory.requests()
    response.request.prepare_url("http://127.0.0.1/items", case.as_transport_kwargs()["params"])

    with pytest.raises(AcceptedNegativeData):
        negative_data_rejection(check_context(), response, case)


# Servers commonly parse these as floats, so they may legitimately read them as numbers.
@pytest.mark.parametrize("value", ["nan", "inf", "-Infinity", "1e400"])
@pytest.mark.parametrize("location", [ParameterLocation.PATH, ParameterLocation.QUERY], ids=["path", "query"])
def test_negative_data_rejection_ignores_non_finite_numeric_wire_value(ctx, response_factory, value, location):
    schema = ctx.openapi.load_schema(
        {
            "/rate/{value}": {
                "get": {
                    "parameters": [
                        {"in": "path", "name": "value", "required": True, "schema": {"type": "number"}},
                        {"in": "query", "name": "value", "required": True, "schema": {"type": "number"}},
                    ]
                }
            }
        }
    )
    mutation = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="value", location=location)
    meta = build_metadata(generation_modes=[GenerationMode.NEGATIVE], mutations=(mutation,))
    meta.components = {location: ComponentInfo(mode=GenerationMode.NEGATIVE)}
    case = schema["/rate/{value}"]["GET"].Case(
        _meta=meta,
        path_parameters={"value": value if location == ParameterLocation.PATH else 1.5},
        query={"value": value if location == ParameterLocation.QUERY else 1.5},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_ignores_huge_integer_wire_value(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/items/{id}": {
                "get": {"parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}]}
            }
        }
    )
    mutation = _mutation(OperatorKind.CHANGE_TYPE, ("type",), parameter="id", location=ParameterLocation.PATH)
    meta = build_metadata(generation_modes=[GenerationMode.NEGATIVE], mutations=(mutation,))
    meta.components = {ParameterLocation.PATH: ComponentInfo(mode=GenerationMode.NEGATIVE)}
    case = schema["/items/{id}"]["GET"].Case(_meta=meta, path_parameters={"id": "9" * 400})
    assert negative_data_rejection(check_context(), response_factory.requests(), case) is None


def test_negative_data_rejection_ignores_non_finite_element_in_query_array(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {"/rates": {"get": {"parameters": [{"in": "query", "name": "rate", "schema": {"type": "number"}}]}}}
    )
    case = schema["/rates"]["GET"].Case(
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
    schema = ctx.openapi.load_schema(
        {
            "/rates/{values}": {
                "get": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "values",
                            "required": True,
                            "schema": {"type": "array", "items": {"type": "number"}},
                        }
                    ]
                }
            }
        }
    )
    case = schema["/rates/{values}"]["GET"].Case(
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
    ("ref", "body", "raises"),
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
def test_negative_data_rejection_read_only_property(ctx, response_factory, ref, body, raises):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}},
                    },
                    "responses": {"201": {"description": "Created"}, "400": {"description": "Bad Request"}},
                }
            }
        },
        components=_READ_ONLY_COMPONENTS,
    )
    case = schema["/items"]["POST"].Case(
        body=body,
        media_type="application/json",
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
    )
    response = response_factory.requests(status_code=201)
    if raises:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


# Only the alternative matching the case media type decides; an undeclared one leaves the failure standing.
@pytest.mark.parametrize(
    ("media_type", "raises"),
    [("application/xml", False), ("application/yaml", True)],
    ids=["declared-media-type", "undeclared-media-type"],
)
def test_negative_data_rejection_read_only_property_media_types(ctx, response_factory, media_type, raises):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            },
                            "application/xml": {"schema": {"$ref": "#/components/schemas/Item"}},
                        },
                    },
                    "responses": {"201": {"description": "Created"}, "400": {"description": "Bad Request"}},
                }
            }
        },
        components=_READ_ONLY_COMPONENTS,
    )
    case = schema["/items"]["POST"].Case(
        body={"name": "", "id": {}},
        media_type=media_type,
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
    )
    response = response_factory.requests(status_code=201)
    if raises:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


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
def test_body_negation_becomes_valid_after_serialization(ctx, media_type, body_mode, query_mode, header_mode, expected):
    schema = ctx.openapi.load_schema(
        {
            "/endpoint": {
                "put": {
                    "parameters": [
                        {"in": "query", "name": "key", "schema": {"type": "integer"}},
                        {"in": "header", "name": "X-Key", "schema": {"type": "integer"}},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {media_type: {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/endpoint"]["PUT"]
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
    assert _body_negation_becomes_valid_after_serialization(case) is expected


MULTIPART = "multipart/form-data"
URLENCODED = "application/x-www-form-urlencoded"


def _load_form_operation(ctx, media_type, properties=None, encoding=None, version="3.0.2"):
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
    schema = ctx.openapi.load_schema(
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
    return schema["/post"]["POST"]


def _sent_body(case):
    kwargs = case.as_transport_kwargs(base_url="http://127.0.0.1")
    body = (
        requests.Request(**{key: kwargs.get(key) for key in ("method", "url", "headers", "data", "files")})
        .prepare()
        .body
    )
    return body.encode() if isinstance(body, str) else body


@pytest.mark.parametrize(
    ("media_type", "properties", "value", "wire"),
    [
        (MULTIPART, None, True, b'name="description"\r\n\r\nTrue\r\n'),
        (URLENCODED, None, True, b"description=true"),
        (MULTIPART, {"count": {"type": "string", "pattern": "^[0-9]+$"}}, 42, b'name="count"\r\n\r\n42\r\n'),
        (MULTIPART, None, 1.5, b'name="description"\r\n\r\n1.5\r\n'),
        (URLENCODED, None, 1.5, b"description=1.5"),
    ],
    ids=["multipart-boolean", "urlencoded-boolean", "multipart-integer", "multipart-float", "urlencoded-float"],
)
def test_negative_data_rejection_accepts_form_scalars_sent_as_valid_strings(
    ctx, response_factory, media_type, properties, value, wire
):
    operation = _load_form_operation(ctx, media_type, properties)
    name = "count" if properties else "description"
    case = operation.Case(
        _meta=build_metadata(body=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        body={"reason": "dmca", name: value},
        media_type=media_type,
    )
    assert wire in _sent_body(case)
    assert negative_data_rejection(check_context(), response_factory.requests(status_code=201), case) is None


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
        (MULTIPART, None, None, "3.0.2", {"description": [True, 1]}, None),
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
        "repeated-field",
    ],
)
def test_negative_data_rejection_reports_form_scalars_invalid_when_sent(
    ctx, response_factory, media_type, properties, encoding, version, body, query
):
    operation = _load_form_operation(ctx, media_type, properties, encoding, version)
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
        negative_data_rejection(check_context(), response_factory.requests(status_code=201), case)


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
        assert "API rejected schema-compliant request" in exc_info.value.title
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
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {"name": "X-API-Token", "in": "header", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/test"]["GET"]
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
        with pytest.raises(Failure):
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


def test_negative_data_rejection_single_element_array_serialization(ctx, response_factory):
    # When a single-element array is generated for negative testing (e.g., [67] for an integer parameter),
    # it serializes to the same query string as a single integer (page=67).
    # The API correctly accepts this as valid, so negative_data_rejection should not fail.

    schema = ctx.openapi.load_schema(
        {
            "/job_info/scroll": {
                "get": {
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
                }
            }
        }
    )

    operation = schema["/job_info/scroll"]["GET"]

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


@pytest.mark.parametrize(
    ("value", "is_accepted_negative"),
    [(["false"], True), ([-1], True), (["5"], False), ([5], False)],
    ids=["non-numeric-string", "below-minimum", "numeric-string", "valid-integer"],
)
def test_negative_data_rejection_single_element_array_element_validity(
    ctx, response_factory, value, is_accepted_negative
):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
        _meta=build_metadata(query=GenerationMode.NEGATIVE, generation_modes=[GenerationMode.NEGATIVE]),
        query={"id": value},
    )
    response = response_factory.requests(status_code=200)
    if is_accepted_negative:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


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
    ("other_value", "other_is_negated", "is_accepted_negative"),
    [("not-an-int", True, True), ("7", False, False)],
    ids=["other-invalid", "other-valid"],
)
def test_negative_data_rejection_valid_array_element_beside_invalid_parameter(
    ctx, response_factory, other_location, other_value, other_is_negated, is_accepted_negative
):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"name": "a", "in": "query", "required": True, "schema": {"type": "integer"}},
                        {"name": "b", "in": other_location.value, "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
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
    case = schema["/items"]["GET"].Case(
        _meta=build_metadata(**components, generation_modes=[GenerationMode.NEGATIVE], mutations=tuple(mutations)),
        query=query,
        headers=headers,
    )
    response = response_factory.requests(status_code=200)
    if is_accepted_negative:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


def test_negative_data_rejection_multiple_mutations_name_parameters(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/api/items": {
                "get": {
                    "parameters": [
                        {"name": "kind", "in": "query", "required": True, "schema": {"type": "string", "minLength": 2}},
                        {"name": "tag", "in": "query", "required": False, "schema": {"type": "string", "minLength": 2}},
                    ],
                    "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )

    operation = schema["/api/items"]["GET"]

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
    schema = ctx.openapi.load_schema(
        {
            "/api/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "kind",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "^\\S+$"},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )

    operation = schema["/api/items"]["GET"]

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

    if reported:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


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
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [parameter],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items"]["GET"].Case(
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

    if allow_empty_value is True:
        assert negative_data_rejection(check_context(), response, case) is None
    else:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)


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
    schema = ctx.openapi.load_schema(
        {
            "/api/items/{ids}": {
                "get": {
                    "parameters": [
                        {
                            "name": "ids",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )
    case = schema["/api/items/{ids}"]["GET"].Case(
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

    if reported:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


def test_negative_data_rejection_path_string_numeric_serialization_with_other_negation(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/api/run/{id}": {
                "post": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "key", "in": "query", "required": False, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )

    operation = schema["/api/run/{id}"]["POST"]

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
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "string"}}},
                        }
                    }
                }
            }
        }
    )
    operation = schema["/test"]["GET"]
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
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
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
                }
            }
        }
    )
    operation = schema["/test"]["GET"]
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
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
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
                }
            }
        }
    )
    case = schema["/test"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(content=b'{"secret": "s"}'), True)

    with pytest.raises(JsonSchemaError) as exc_info:
        response_schema_conformance(_CHECK_CTX, response, case)
    assert exc_info.value.message.startswith('Property "secret" is not allowed')


def test_response_schema_conformance_reports_boolean_branch_rejection(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"allOf": [{"type": "object"}, False]}}},
                        }
                    }
                }
            }
        },
        version="3.1.0",
    )
    case = schema["/test"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(content=b"{}"), True)

    with pytest.raises(JsonSchemaError):
        response_schema_conformance(_CHECK_CTX, response, case)


def test_response_schema_conformance_validate_formats_disabled(ctx, response_factory):
    config = SchemathesisConfig.from_dict({"checks": {"response_schema_conformance": {"validate-formats": False}}})
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_DATE_TIME_PATHS), config=config)
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
    ],
)
def test_response_schema_conformance_discriminator(ctx, response_factory, body, discriminator, should_fail):
    schema = _discriminator_schema(ctx, discriminator=discriminator)
    operation = schema["/pets"]["GET"]
    case = operation.Case()
    response = response_factory.requests(content=json.dumps(body).encode())
    response = Response.from_requests(response, True)

    if should_fail:
        with pytest.raises(Failure) as exc_info:
            response_schema_conformance(_CHECK_CTX, response, case)
        assert exc_info.value.title == "Discriminator value not in schema mapping"
    else:
        assert response_schema_conformance(_CHECK_CTX, response, case) is None


def test_response_schema_conformance_discriminator_boolean_schema(ctx, response_factory):
    # Boolean schemas (true/false) in anyOf/oneOf are valid in OpenAPI 3.1.
    # The boolean item is skipped during implicit mapping extraction; only $ref items contribute.
    schema = ctx.openapi.load_schema(
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
                                            True,
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
        version="3.1.0",
        components={
            "schemas": {
                "Cat": {"type": "object", "properties": {"petType": {"type": "string"}}},
            }
        },
    )
    operation = schema["/pets"]["GET"]
    case = operation.Case()

    valid = response_factory.requests(content=b'{"petType": "Cat"}')
    assert response_schema_conformance(_CHECK_CTX, Response.from_requests(valid, True), case) is None

    invalid = response_factory.requests(content=b'{"petType": "Fish"}')
    with pytest.raises(Failure) as exc_info:
        response_schema_conformance(_CHECK_CTX, Response.from_requests(invalid, True), case)
    assert exc_info.value.title == "Discriminator value not in schema mapping"


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
    with pytest.raises(Failure) as exc_info:
        response_schema_conformance(_CHECK_CTX, invalid, case)
    assert exc_info.value.title == "Discriminator value not in schema mapping"


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
        ({"Age": "0", "X-Cache": "MISS"}, ""),
        (None, ""),
    ],
    ids=["age", "x-cache-hit", "fresh", "no-cache-headers"],
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
    operation = ctx.openapi.load_schema(
        {"/items": {"get": {"responses": {"200": {"description": "OK", "content": {"application/json": {}}}}}}}
    )["/items"]["GET"]
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
    schema = ctx.openapi.load_schema(
        {
            "/items/{item_id}": {
                "get": {
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/items/{item_id}"]["GET"].Case(
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
    schema = ctx.openapi.load_schema(
        {
            "/items/{item_id}": {
                "delete": {
                    "parameters": [
                        {"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "X-Token", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"204": {"description": "Deleted"}},
                }
            }
        }
    )
    case = schema["/items/{item_id}"]["DELETE"].Case(
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
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    **({"security": [{"basicAuth": []}]} if secured else {}),
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
    )
    case = schema["/items"]["GET"].Case(
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


def test_positive_data_acceptance_configured_dynamic_auth_path(ctx, response_factory):
    schema = ctx.openapi.load_schema(_token_endpoint_paths())
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
    schema = ctx.openapi.load_schema(
        {"/token": {"post": {"responses": {"200": {"description": "OK"}}}}},
        version="2.0",
        basePath="/api/v1",
        securityDefinitions={
            "oauth2": {"type": "oauth2", "flow": "password", "tokenUrl": "/api/v1/token", "scopes": {}}
        },
    )
    case = schema["/token"]["POST"].Case(_meta=build_metadata())
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
        ({"Allow": "GET, HEAD, OPTIONS"}, "OPTIONS", "missing documented methods: POST"),
        (
            {"Allow": "GET, POST, DELETE"},
            "OPTIONS",
            "undocumented methods advertised: DELETE",
        ),
        (
            {"Allow": "GET, DELETE"},
            "OPTIONS",
            "missing documented methods: POST; undocumented methods advertised: DELETE",
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


def _negative_case(operation, location, parameter, mutation=None, **kwargs):
    return operation.Case(
        _meta=build_metadata(
            generation_modes=[GenerationMode.NEGATIVE],
            parameter=parameter,
            parameter_location=location,
            mutations=(mutation,) if mutation else (),
            description="Invalid component",
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
def test_negative_data_rejection_single_parameter(
    ctx, response_factory, location, parameter_schema, value, type_mutation, reported
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
    case = _negative_case(operation, location, "value", mutation, **{location.container_name: {"value": value}})
    response = response_factory.requests(status_code=200)
    if reported:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(check_context(), response, case)
    else:
        assert negative_data_rejection(check_context(), response, case) is None


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
    operation = ctx.openapi.load_schema(
        {"/items/{key}": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}}
    )["/items/{key}"]["GET"]
    case = _negative_case(
        operation,
        location,
        "key",
        _mutation(mutation, ("type",), parameter="key", location=location) if mutation else None,
        **{location.container_name: {"key": value}},
    )
    assert negative_data_rejection(check_context(), response_factory.requests(status_code=200), case) is None


def test_negative_data_rejection_reports_header_beside_query_rejected_by_validator(ctx, response_factory):
    operation = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "integer", "multipleOf": 0}},
                        {"name": "X-Id", "in": "header", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )["/items"]["GET"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            headers=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid component",
        ),
        query={"q": "abc"},
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
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {"required": True, "content": content},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
        components=components,
    )
    case = schema["/foo"]["POST"].Case(body=body, media_type=media_type, _meta=build_metadata())
    with pytest.raises(RejectedPositiveData) as exc:
        positive_data_acceptance(check_context(), _opaque_rejection(response_factory), case)
    assert (
        exc.value.message == f"Valid data should have been accepted\nExpected: 2xx, 401, 403, 404, 409, 429, 5xx{hint}"
    )


def test_additional_properties_hint_ignores_blame_on_query_parameter(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": _NAME_PROPERTY}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/foo"]["POST"].Case(
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
        f"Valid data should have been accepted\nExpected: 2xx, 401, 403, 404, 409, 429, 5xx{_EXTRA_PROPERTY_HINT}"
    )


# An implicit flow has no token endpoint, so no operation is a credential grant.
def test_positive_data_acceptance_reports_rejection_on_implicit_oauth_flow_operation(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {"/token": {"post": {"responses": {"200": {"description": "OK"}}}}},
        components={
            "securitySchemes": {
                "oauth": {
                    "type": "oauth2",
                    "flows": {"implicit": {"authorizationUrl": "/token", "scopes": {}}},
                }
            }
        },
    )
    case = schema["/token"]["POST"].Case(_meta=build_metadata())
    with pytest.raises(RejectedPositiveData):
        positive_data_acceptance(check_context(), _opaque_rejection(response_factory), case)
