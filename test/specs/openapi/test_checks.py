import json

import pytest
import yaml

import schemathesis
from schemathesis.checks import CheckContext
from schemathesis.config._checks import ChecksConfig
from schemathesis.core.failures import AcceptedNegativeData, Failure, MalformedJson
from schemathesis.core.mutations import OperatorKind
from schemathesis.core.parameters import ParameterLocation
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
from schemathesis.openapi.checks import UseAfterFree
from schemathesis.specs.openapi.checks import (
    ResourcePath,
    _additional_properties_hint,
    _body_negation_becomes_valid_after_serialization,
    _is_prefix_operation,
    has_only_additional_properties_in_non_body_parameters,
    missing_required_header,
    negative_data_rejection,
    positive_data_acceptance,
    response_schema_conformance,
    use_after_free,
)
from schemathesis.specs.openapi.negative.mutations import Mutation, MutationChannel


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
                    schema_pointer=f"/properties/{parameter}",
                    channel=MutationChannel.SCHEMA,
                    operator=OperatorKind.CHANGE_TYPE,
                    keywords=("type",),
                    parameter=parameter,
                    original_value=None,
                    new_value=None,
                ),
            )
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


def _mutation(operator, keywords, parameter=None):
    return Mutation(
        path=(parameter,) if parameter else (),
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


@pytest.mark.parametrize(
    ("body", "expected_hint"),
    [
        pytest.param({"a": 1, "b": {"x": "q"}}, None, id="declared-keys-only"),
        pytest.param({"a": 1, "b": {"x": "q"}, "extra": "yes"}, "`extra`", id="real-extra-fires"),
    ],
)
def test_additional_properties_hint_resolves_bundled_ref(ctx, body, expected_hint):
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
    hint = _additional_properties_hint(case)
    if expected_hint is None:
        assert hint is None, f"False positive: {hint!r}"
    else:
        assert hint is not None and expected_hint in hint, f"Expected mention of {expected_hint} in {hint!r}"


@pytest.mark.parametrize(
    ("status_code", "should_raise"),
    [
        pytest.param(405, False, id="405-method-not-allowed-passes"),
        # 409 short-circuits validation on uniqueness-gated endpoints (duplicate email etc.)
        # before the server reaches the mutated field — treating it as "accepted" is a false positive.
        pytest.param(409, False, id="409-conflict-passes"),
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
    ctx = CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=ChecksConfig(),
        transport_kwargs=None,
    )
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
            CheckContext(
                override=None,
                auth=None,
                headers=None,
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            response,
            case,
        )
        is None
    )


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
        body={},
        media_type=media_type,
    )
    assert _body_negation_becomes_valid_after_serialization(case) is expected


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
        CheckContext(
            override=None,
            auth=None,
            headers=None,
            config=ChecksConfig(),
            transport_kwargs=None,
        ),
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
    ctx = CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=ChecksConfig.from_dict({"positive_data_acceptance": {"expected-statuses": expected_statuses}}),
        transport_kwargs=None,
    )

    if should_raise:
        with pytest.raises(Failure) as exc_info:
            positive_data_acceptance(ctx, response, case)
        assert "API rejected schema-compliant request" in exc_info.value.title
    else:
        assert positive_data_acceptance(ctx, response, case) is None


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
    check_ctx = CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=ChecksConfig(),
        transport_kwargs=None,
    )
    if should_raise:
        with pytest.raises(Failure):
            missing_required_header(check_ctx, response, case)
    else:
        assert missing_required_header(check_ctx, response, case) is None


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
        CheckContext(
            override=None,
            auth=None,
            headers=None,
            config=ChecksConfig(),
            transport_kwargs=None,
        ),
        response,
        case,
    )

    # Should return None (no error) because the serialized value is valid
    assert result is None


def test_negative_data_rejection_multi_element_array_with_valid_element(ctx, response_factory):
    # See GH-3697
    # Multi-element arrays in query parameters serialize as repeated keys: [True, 1] -> ?page_size=True&page_size=1
    # Some frameworks (e.g. Django/DRF) pick one value from repeated keys. If that value is a valid integer (1),
    # the request is accepted and should NOT trigger negative_data_rejection.
    schema = ctx.openapi.load_schema(
        {
            "/api/model-fk/user/": {
                "get": {
                    "parameters": [
                        {
                            "name": "page_size",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
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

    operation = schema["/api/model-fk/user/"]["GET"]

    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid type array (expected integer)",
            parameter="page_size",
            parameter_location=ParameterLocation.QUERY,
        ),
        query={"page_size": [True, 1]},
    )

    response = response_factory.requests(status_code=200)

    result = negative_data_rejection(
        CheckContext(
            override=None,
            auth=None,
            headers=None,
            config=ChecksConfig(),
            transport_kwargs=None,
        ),
        response,
        case,
    )

    assert result is None


def test_negative_data_rejection_multi_element_array_string_numeric_element(ctx, response_factory):
    # GH-3931: a string element like "44" inside the array serializes to the wire form
    # `?page_size=44`, which Django parses as integer 44. The strict JSON Schema validator
    # treats "44" as a string (not integer), but the server accepts it.
    schema = ctx.openapi.load_schema(
        {
            "/api/model-fk/user/": {
                "get": {
                    "parameters": [
                        {
                            "name": "page_size",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
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

    operation = schema["/api/model-fk/user/"]["GET"]

    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid type array (expected integer)",
            parameter="page_size",
            parameter_location=ParameterLocation.QUERY,
        ),
        query={
            "page_size": [
                -1.2097890770124232e65,
                {"a": None},
                [[-8.080921524865554e-19], "x"],
                [],
                "44",
            ]
        },
    )

    response = response_factory.requests(status_code=200)

    result = negative_data_rejection(
        CheckContext(
            override=None,
            auth=None,
            headers=None,
            config=ChecksConfig(),
            transport_kwargs=None,
        ),
        response,
        case,
    )

    assert result is None


def test_negative_data_rejection_query_object_mutation_with_numeric_key(ctx, response_factory):
    # Negative type mutation can turn an integer query into an object. urlencode(doseq=True)
    # iterates dict keys, so {"5": "x"} produces ?id=5 — the server parses 5 as integer
    # and the request becomes effectively valid.
    schema = ctx.openapi.load_schema(
        {
            "/api/items": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": False, "schema": {"type": "integer"}}],
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
            description="Invalid type object (expected integer)",
            parameter="id",
            parameter_location=ParameterLocation.QUERY,
        ),
        query={"id": {"5": "x"}},
    )
    response = response_factory.requests(status_code=200)

    assert (
        negative_data_rejection(
            CheckContext(
                override=None,
                auth=None,
                headers=None,
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            response,
            case,
        )
        is None
    )


def test_negative_data_rejection_path_string_numeric_serialization(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/api/run/{id}": {
                "post": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Success"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )

    operation = schema["/api/run/{id}"]["POST"]

    case = operation.Case(
        _meta=build_metadata(
            path_parameters=GenerationMode.NEGATIVE,
            generation_modes=[GenerationMode.NEGATIVE],
            description="Invalid type string (expected integer)",
            parameter="id",
            parameter_location=ParameterLocation.PATH,
        ),
        # Encoded `+1` decodes back to an integer-like value accepted by many servers
        path_parameters={"id": "%2B1"},
    )
    response = response_factory.requests(status_code=200)

    assert (
        negative_data_rejection(
            CheckContext(
                override=None,
                auth=None,
                headers=None,
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            response,
            case,
        )
        is None
    )


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
            CheckContext(
                override=None,
                auth=None,
                headers=None,
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
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
            CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None),
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


_CHECK_CTX = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)


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


def _build_user_profile_chain(ctx, response_factory, *, delete_status: int):
    schema = ctx.openapi.load_schema(_USER_PROFILE_SCHEMA)
    post_operation = schema["/users"]["POST"]
    delete_operation = schema["/users/{userId}"]["DELETE"]
    get_operation = schema["/users/{userId}/profile"]["GET"]

    post_case = post_operation.Case(body={"name": "alice"})
    delete_case = delete_operation.Case(path_parameters={"userId": "alice"})
    get_case = get_operation.Case(path_parameters={"userId": "alice"})

    post_response = Response.from_requests(response_factory.requests(status_code=201), True)
    delete_response = Response.from_requests(response_factory.requests(status_code=delete_status), True)
    get_response = Response.from_requests(response_factory.requests(status_code=200), True)

    recorder = ScenarioRecorder(label="use-after-free-test")
    recorder.record_case(parent_id=None, case=post_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=post_case.id, response=post_response)
    recorder.record_case(parent_id=post_case.id, case=delete_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=delete_case.id, response=delete_response)
    recorder.record_case(parent_id=delete_case.id, case=get_case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=get_case.id, response=get_response)

    check_context = CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=ChecksConfig(),
        transport_kwargs=None,
        recorder=recorder,
    )
    return check_context, get_case, get_response


@pytest.mark.parametrize("delete_status", [500, 404], ids=["server-crash", "not-found"])
def test_use_after_free_skips_when_delete_failed(ctx, response_factory, delete_status):
    # When DELETE returns 5xx (server crash) or 404 (nothing to free), the resource was never
    # actually deleted, so a subsequent 2xx read is not a use-after-free.
    check_context, get_case, get_response = _build_user_profile_chain(
        ctx, response_factory, delete_status=delete_status
    )
    assert use_after_free(check_context, get_response, get_case) is None


def test_use_after_free_fires_when_delete_succeeded(ctx, response_factory):
    check_context, get_case, get_response = _build_user_profile_chain(ctx, response_factory, delete_status=204)
    with pytest.raises(UseAfterFree):
        use_after_free(check_context, get_response, get_case)
