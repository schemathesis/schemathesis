from unittest.mock import ANY

import pytest
from hypothesis import Phase, settings

import schemathesis
from schemathesis._hypothesis import create_test
from schemathesis.constants import NOT_SET
from schemathesis.experimental import COVERAGE_PHASE
from schemathesis.generation._methods import DataGenerationMethod
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER

from test.utils import assert_requests_call


@pytest.fixture(autouse=True)
def with_phase():
    COVERAGE_PHASE.enable()


POSITIVE_CASES = [
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 5, "q2": "0000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 6, "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "00"}, "query": {"q1": 5, "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "4", "h2": "000"}, "query": {"q1": 5, "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 5, "q2": "000"}, "body": {"x-prop": ""}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 5, "q2": "000"}, "body": {"j-prop": 0}},
]
NEGATIVE_CASES = [
    {"body": 0, "headers": {"h1": ANY, "h2": "0"}, "query": {"q1": ANY, "q2": "00"}},
    {"body": 0, "headers": {"h1": ANY, "h2": "0"}, "query": {"q1": 4, "q2": 0}},
    {"body": 0, "headers": {"h1": ANY, "h2": "0000"}, "query": {"q1": ANY, "q2": 0}},
    {"body": 0, "headers": {"h1": "6", "h2": "0"}, "query": {"q1": ANY, "q2": 0}},
    {"body": {}, "headers": {"h1": ANY, "h2": "0"}, "query": {"q1": ANY, "q2": 0}},
    {
        "body": {"x-prop": 0},
        "headers": {"h1": ANY, "h2": "0"},
        "query": {"q1": ANY, "q2": 0},
    },
    {"body": 0, "headers": {"h1": ANY, "h2": "0"}, "query": {"q1": ANY, "q2": 0}},
    {"body": {}, "headers": {"h1": ANY, "h2": "0"}, "query": {"q1": ANY, "q2": 0}},
    {
        "body": {"j-prop": ANY},
        "headers": {"h1": ANY, "h2": "0"},
        "query": {"q1": ANY, "q2": 0},
    },
    {"body": 0, "headers": {"h1": ANY, "h2": "0"}, "query": {"q1": ANY, "q2": 0}},
]
MIXED_CASES = [
    {"query": {"q1": 5, "q2": "00"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": 0}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "0000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 4, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 6, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "0000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "0"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "00"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "6", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": ANY, "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "4", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": ""}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": ANY}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
]


@pytest.mark.parametrize(
    "methods, expected",
    (
        (
            [DataGenerationMethod.positive],
            POSITIVE_CASES,
        ),
        (
            [DataGenerationMethod.negative],
            NEGATIVE_CASES,
        ),
        (
            [DataGenerationMethod.positive, DataGenerationMethod.negative],
            MIXED_CASES,
        ),
    ),
)
def test_phase(empty_open_api_3_schema, methods, expected):
    empty_open_api_3_schema["paths"] = {
        "/foo": {
            "post": {
                "parameters": [
                    {
                        "in": "query",
                        "name": "q1",
                        "schema": {"type": "integer", "minimum": 5},
                        "required": True,
                    },
                    {
                        "in": "query",
                        "name": "q2",
                        "schema": {"type": "string", "minLength": 3},
                        "required": True,
                    },
                    {
                        "in": "header",
                        "name": "h1",
                        "schema": {"type": "integer", "maximum": 5},
                        "required": True,
                    },
                    {
                        "in": "header",
                        "name": "h2",
                        "schema": {"type": "string", "maxLength": 3},
                        "required": True,
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"j-prop": {"type": "integer"}},
                                "required": ["j-prop"],
                            },
                        },
                        "application/xml": {
                            "schema": {
                                "type": "object",
                                "properties": {"x-prop": {"type": "string"}},
                                "required": ["x-prop"],
                            },
                        },
                    },
                },
                "responses": {"default": {"description": "OK"}},
            }
        },
    }
    assert_coverage(empty_open_api_3_schema, methods, expected)


def test_phase_no_body(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/foo": {
            "post": {
                "parameters": [
                    {
                        "in": "query",
                        "name": "q1",
                        "schema": {"type": "integer", "minimum": 5},
                        "required": True,
                    },
                ],
                "responses": {"default": {"description": "OK"}},
            }
        },
    }
    assert_coverage(
        empty_open_api_3_schema, [DataGenerationMethod.positive], [{"query": {"q1": 6}}, {"query": {"q1": 5}}]
    )


def assert_coverage(schema, methods, expected):
    schema = schemathesis.from_dict(schema)

    cases = []
    operation = schema["/foo"]["post"]

    def test(case):
        assert_requests_call(case)
        if len(methods) == 1:
            assert case.data_generation_method == methods[0]
        output = {}
        for container in LOCATION_TO_CONTAINER.values():
            value = getattr(case, container)
            if value is not None and value is not NOT_SET:
                output[container] = value
        cases.append(output)

    test_func = create_test(
        operation=operation, test=test, data_generation_methods=methods, settings=settings(phases=[Phase.explicit])
    )

    test_func()

    assert cases == expected
