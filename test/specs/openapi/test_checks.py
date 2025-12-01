import pytest
import yaml

import schemathesis
from schemathesis.checks import CheckContext
from schemathesis.config._checks import ChecksConfig
from schemathesis.core.failures import Failure
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoverageScenario,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.specs.openapi.checks import (
    ResourcePath,
    _body_negation_becomes_valid_after_serialization,
    _is_prefix_operation,
    has_only_additional_properties_in_non_body_parameters,
    negative_data_rejection,
    positive_data_acceptance,
    response_schema_conformance,
)


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
    path_parameters=None, query=None, headers=None, cookies=None, body=None, generation_mode=GenerationMode.POSITIVE
):
    return CaseMetadata(
        generation=GenerationInfo(
            time=0.1,
            mode=generation_mode,
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
                description="",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
        ),
    )


@pytest.fixture
def sample_schema(ctx):
    return ctx.openapi.build_schema(
        {
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
    )


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
    schema = schemathesis.openapi.from_dict(sample_schema)
    operation = schema["/test"]["POST"]
    case = operation.Case(**kwargs)
    assert has_only_additional_properties_in_non_body_parameters(case) is expected


def test_negative_data_rejection_on_additional_properties(response_factory, sample_schema):
    # See GH-2312
    response = response_factory.requests()
    schema = schemathesis.openapi.from_dict(sample_schema)
    operation = schema["/test"]["POST"]
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_mode=GenerationMode.NEGATIVE,
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
    text_plain_schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(text_plain_schema)
    operation = schema["/endpoint"]["PUT"]
    case = operation.Case(
        _meta=build_metadata(
            body=body_mode,
            query=query_mode,
            headers=header_mode,
            generation_mode=GenerationMode.NEGATIVE,
        ),
        body={},
        media_type=media_type,
    )
    assert _body_negation_becomes_valid_after_serialization(case) is expected


def test_response_schema_conformance_with_unspecified_method(response_factory, sample_schema):
    response = response_factory.requests()
    response = Response.from_requests(response, True)
    sample_schema["paths"]["/test"]["post"]["responses"] = {
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
    schema = schemathesis.openapi.from_dict(sample_schema)
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
    schema = schemathesis.openapi.from_dict(sample_schema)
    operation = schema["/test"]["POST"]
    response = response_factory.requests(status_code=status_code)
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.POSITIVE if is_positive else GenerationMode.NEGATIVE,
            generation_mode=GenerationMode.POSITIVE if is_positive else GenerationMode.NEGATIVE,
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
@pytest.mark.operations("success", "basic")
def test_missing_required_header(ctx, cli, openapi3_base_url, snapshot_cli, path, header_name, expected_status):
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
            f"--url={openapi3_base_url}",
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


@pytest.mark.operations("success")
def test_missing_required_accept_header(ctx, cli, openapi3_base_url, tmp_path):
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
        f"--url={openapi3_base_url}",
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
@pytest.mark.operations("basic")
def test_missing_required_authorization_if_provided_explicitly(cli, openapi3_schema_url, tmp_path, arg):
    cassette_path = tmp_path / "missing_authorization_header.yaml"

    cli.run(
        openapi3_schema_url,
        f"--report-vcr-path={cassette_path}",
        "--phases=coverage",
        "--mode=negative",
        "--checks=missing_required_header",
        "--max-examples=1",
        arg,
    )

    verify_missing_required_header(cassette_path, "Authorization", "SUCCESS")


@pytest.mark.parametrize("path, method", [("/success", "get"), ("/basic", "post")])
@pytest.mark.operations("success")
def test_method_not_allowed(ctx, cli, openapi3_base_url, snapshot_cli, path, method):
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
            f"--url={openapi3_base_url}",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


def test_negative_data_rejection_single_element_array_serialization(ctx, response_factory):
    # When a single-element array is generated for negative testing (e.g., [67] for an integer parameter),
    # it serializes to the same query string as a single integer (page=67).
    # The API correctly accepts this as valid, so negative_data_rejection should not fail.

    raw_schema = ctx.openapi.build_schema(
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

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/job_info/scroll"]["GET"]

    # Simulate negative testing where a single-element array [67] is generated
    # for an integer parameter
    case = operation.Case(
        _meta=build_metadata(
            query=GenerationMode.NEGATIVE,
            generation_mode=GenerationMode.NEGATIVE,
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
