import pytest

import schemathesis
from schemathesis.exceptions import CheckFailed
from schemathesis.generation import DataGenerationMethod
from schemathesis.internal.checks import CheckConfig, CheckContext, PositiveDataAcceptanceConfig
from schemathesis.models import Case, GenerationMetadata, TestPhase
from schemathesis.specs.openapi.checks import (
    ResourcePath,
    _is_prefix_operation,
    has_only_additional_properties_in_non_body_parameters,
    negative_data_rejection,
    positive_data_acceptance,
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


def build_metadata(path_parameters=None, query=None, headers=None, cookies=None, body=None):
    return GenerationMetadata(
        path_parameters=path_parameters,
        query=query,
        headers=headers,
        cookies=cookies,
        body=body,
        phase=TestPhase.GENERATE,
        description=None,
        location=None,
        parameter=None,
        parameter_location=None,
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
                            "in": "headers",
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
            {"meta": build_metadata(body=DataGenerationMethod.negative)},
            False,
        ),
        (
            {
                "query": {"key": 1},
                "meta": build_metadata(query=DataGenerationMethod.negative),
            },
            False,
        ),
        (
            {
                "query": {"key": 1},
                "headers": {"X-Key": 42},
                "meta": build_metadata(query=DataGenerationMethod.negative),
            },
            False,
        ),
        (
            {
                "query": {"key": 5, "unknown": 3},
                "meta": build_metadata(query=DataGenerationMethod.negative),
            },
            True,
        ),
        (
            {
                "query": {"key": 5, "unknown": 3},
                "headers": {"X-Key": 42},
                "meta": build_metadata(query=DataGenerationMethod.negative),
            },
            True,
        ),
    ],
)
def test_has_only_additional_properties_in_non_body_parameters(sample_schema, kwargs, expected):
    schema = schemathesis.from_dict(sample_schema)
    operation = schema["/test"]["POST"]
    case = Case(operation=operation, generation_time=0.0, **kwargs)
    assert has_only_additional_properties_in_non_body_parameters(case) is expected


def test_negative_data_rejection_on_additional_properties(response_factory, sample_schema):
    # See GH-2312
    response = response_factory.requests()
    schema = schemathesis.from_dict(sample_schema)
    operation = schema["/test"]["POST"]
    case = Case(
        operation=operation,
        generation_time=0.0,
        meta=build_metadata(query=DataGenerationMethod.negative),
        data_generation_method=DataGenerationMethod.negative,
        query={"key": 5, "unknown": 3},
    )
    assert negative_data_rejection(CheckContext(override=None, auth=None, headers=None), response, case) is None


@pytest.mark.parametrize(
    ("status_code", "allowed_statuses", "is_positive", "should_raise"),
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
    allowed_statuses,
    is_positive,
    should_raise,
):
    schema = schemathesis.from_dict(sample_schema)
    operation = schema["/test"]["POST"]
    response = response_factory.requests(status_code=status_code)
    case = Case(
        operation=operation,
        generation_time=0.0,
        meta=build_metadata(query=DataGenerationMethod.positive if is_positive else DataGenerationMethod.negative),
        data_generation_method=DataGenerationMethod.positive if is_positive else DataGenerationMethod.negative,
    )
    ctx = CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=CheckConfig(positive_data_acceptance=PositiveDataAcceptanceConfig(allowed_statuses=allowed_statuses)),
    )

    if should_raise:
        with pytest.raises(CheckFailed) as exc_info:
            positive_data_acceptance(ctx, response, case)
        assert "Rejected positive data" in str(exc_info.value)
    else:
        assert positive_data_acceptance(ctx, response, case) is None
