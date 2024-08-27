import pytest

import schemathesis
from schemathesis.generation import DataGenerationMethod
from schemathesis.models import Case, GenerationMetadata, TestPhase
from schemathesis.specs.openapi.checks import (
    ResourcePath,
    _is_prefix_operation,
    has_only_additional_properties_in_non_body_parameters,
    negative_data_rejection,
)


@pytest.mark.parametrize(
    "lhs, lhs_vars, rhs, rhs_vars, expected",
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
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
        # LHS is a prefix of RHS, with different variable types
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": 123}, True),
        ("/users/{id}", {"id": 123}, "/users/{id}/details", {"id": "123"}, True),
        # LHS is a prefix of RHS, with extra path segments
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view/edit", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view/edit", {"id": "456"}, False),
        # LHS is a prefix of RHS
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
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
    )


@pytest.fixture
def sample_schema(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    return empty_open_api_3_schema


@pytest.mark.parametrize(
    "kwargs, expected",
    (
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
    ),
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
    assert negative_data_rejection(response, case) is None
