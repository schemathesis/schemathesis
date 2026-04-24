import json
import re
import uuid
from dataclasses import dataclass
from unittest.mock import ANY
from urllib.parse import parse_qs, unquote

import jsonschema_rs
import pytest
from flask import jsonify, request
from hypothesis import Phase, settings
from hypothesis import strategies as st
from hypothesis.errors import Unsatisfiable
from requests import Request
from requests.models import RequestEncodingMixin

import schemathesis
from schemathesis.checks import CheckContext
from schemathesis.config import ChecksConfig
from schemathesis.config._projects import ProjectConfig
from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import AcceptedNegativeData
from schemathesis.core.parameters import LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.generation import GenerationMode
from schemathesis.generation import coverage as coverage_generation
from schemathesis.generation.hypothesis.builder import (
    HypothesisTestConfig,
    HypothesisTestMode,
    _iter_coverage_cases,
    create_test,
    generate_coverage_cases,
)
from schemathesis.generation.meta import CoverageScenario, TestPhase
from schemathesis.specs.openapi.checks import negative_data_rejection
from test.utils import assert_requests_call


@dataclass
class Pattern:
    _pattern: str

    def __eq__(self, value: object, /) -> bool:
        return bool(isinstance(value, str) and re.match(self._pattern, value))


POSITIVE_CASES = [
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "5", "q2": "0000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "6", "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "00"}, "query": {"q1": "5", "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "4", "h2": "000"}, "query": {"q1": "5", "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "5", "q2": "000"}, "body": {"x-prop": ""}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "5", "q2": "000"}, "body": {"j-prop": 0}},
]
NEGATIVE_CASES = [
    {"query": {"q1": ANY}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": ["0", "0"]}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": [ANY, ANY], "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "00"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": "4", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {
        "query": {"q1": ["null", "null"], "q2": "0"},
        "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")},
        "body": {"j-prop": 0},
    },
    {"query": {"q1": "", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": "null", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": "false", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "0000"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": ANY}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "null,null"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "null"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "false"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": ANY}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "6", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "{}", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "null,null", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "null", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "false", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": [None, None]},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": False},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": {}}},
    {
        "query": {"q1": ANY, "q2": "0"},
        "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")},
        "body": {"j-prop": [None, None]},
    },
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": ""}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": None}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": False}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": ANY}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": [None, None]},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": ""},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": False},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
]
MIXED_CASES = [
    {"query": {"q1": "5"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": ["000", "000"]}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": ["5", "5"], "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "00"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "0"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "0000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "4", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": ["null", "null"], "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "null", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "false", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "6", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "0000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": ANY}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "null,null"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "null"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "false"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": ANY}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": ANY}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "00"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "6", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "{}", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "null,null", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "null", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "false", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": ANY, "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "4", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": False},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": ""}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": {}}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": [None, None]}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": ""}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": None}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": False}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": ANY}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": ""},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": False},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
]


def build_schema(ctx, parameters=None, request_body=None, responses=None, version="3.0.2", path="/foo", method="post"):
    if responses is None:
        responses = {"default": {"description": "OK"}}

    schema = {
        path: {
            method: {
                "responses": responses,
            }
        }
    }
    if parameters is not None:
        schema[path][method]["parameters"] = parameters

    if request_body is not None:
        schema[path][method]["requestBody"] = request_body

    return ctx.openapi.build_schema(schema, version=version)


def assert_positive_coverage(schema, expected, path=None):
    return assert_coverage(schema, [GenerationMode.POSITIVE], expected, path)


def assert_negative_coverage(schema, expected, path=None):
    return assert_coverage(schema, [GenerationMode.NEGATIVE], expected, path)


ALL_MODES = list(GenerationMode)


def run_test(operation, test, modes=ALL_MODES, generate_duplicate_query_parameters=None, unexpected_methods=None):
    config = ProjectConfig()
    config.generation.update(modes=modes)
    if generate_duplicate_query_parameters is not None:
        config.phases.coverage.generate_duplicate_query_parameters = generate_duplicate_query_parameters
    if unexpected_methods is not None:
        config.phases.coverage.unexpected_methods = unexpected_methods
    config.phases.examples.enabled = False
    config.phases.fuzzing.enabled = False
    config.phases.stateful.enabled = False
    test_func = create_test(
        operation=operation,
        test_func=test,
        config=HypothesisTestConfig(
            modes=[HypothesisTestMode.COVERAGE],
            project=config,
            settings=settings(phases=[Phase.explicit]),
        ),
    )

    test_func()


def run_positive_test(operation, test, **kwargs):
    return run_test(operation, test, [GenerationMode.POSITIVE], **kwargs)


def run_negative_test(operation, test, **kwargs):
    return run_test(operation, test, [GenerationMode.NEGATIVE], **kwargs)


def collect_coverage_cases(ctx, body_schema, positive=False, version="3.0.2"):
    """Build schema, run test, and return coverage phase cases.

    Always validates that:
    - Positive cases produce bodies that pass JSON schema validation
    - Negative cases produce bodies that fail JSON schema validation
    """
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {"application/json": {"schema": body_schema}},
        },
        version=version,
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]
    validator_cls = operation.schema.adapter.jsonschema_validator_cls
    validator = validator_cls(body_schema, validate_formats=True)
    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            is_valid = validator.is_valid(case.body)
            body_is_target = case.meta.phase.data.parameter_location == ParameterLocation.BODY
            if positive and not is_valid:
                errors = list(validator.iter_errors(case.body))
                pytest.fail(
                    f"Positive case produced invalid body.\n"
                    f"Body: {case.body}\n"
                    f"Schema: {body_schema}\n"
                    f"Validator: {validator_cls.__name__}\n"
                    f"Errors: {[e.message for e in errors]}"
                )
            if not positive and body_is_target and is_valid:
                pytest.fail(
                    f"Negative case produced valid body (should be invalid).\n"
                    f"Body: {case.body}\n"
                    f"Schema: {body_schema}\n"
                    f"Validator: {validator_cls.__name__}\n"
                    f"Scenario: {case.meta.phase.data.scenario}"
                )
            cases.append(case)

    if positive:
        run_positive_test(operation, collect)
    else:
        run_negative_test(operation, collect)

    return cases


@pytest.mark.parametrize(
    ("methods", "expected"),
    [
        (
            [GenerationMode.POSITIVE],
            POSITIVE_CASES,
        ),
        (
            [GenerationMode.NEGATIVE],
            NEGATIVE_CASES,
        ),
        (
            [GenerationMode.POSITIVE, GenerationMode.NEGATIVE],
            MIXED_CASES,
        ),
    ],
)
def test_phase(ctx, methods, expected):
    schema = build_schema(
        ctx,
        [
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
        {
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
    )
    assert_coverage(schema, methods, expected)


def test_phase_no_body(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "q1",
                "schema": {"type": "integer", "minimum": 5},
                "required": True,
            },
        ],
    )
    assert_positive_coverage(schema, [{"query": {"q1": "6"}}, {"query": {"q1": "5"}}])


def test_with_example(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "q1",
                "schema": {"type": "string", "example": "secret"},
                "required": True,
            },
        ],
    )
    assert_positive_coverage(schema, [{"query": {"q1": "secret"}}])


EXPECTED_EXAMPLES = [
    {"query": {"q1": "A1", "q2": "20"}},
    {"query": {"q1": "B2", "q2": "10"}},
    {"query": {"q1": "A1", "q2": "10"}},
]


def test_with_examples_openapi_3(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "q1",
                "schema": {"type": "string"},
                "required": True,
                "examples": {
                    "first": {"value": "A1"},
                    "second": {"value": "B2"},
                },
            },
            {
                "in": "query",
                "name": "q2",
                "schema": {"type": "integer"},
                "required": True,
                "examples": {
                    "first": {"value": 10},
                    "second": {"value": 20},
                },
            },
        ],
    )
    assert_positive_coverage(schema, EXPECTED_EXAMPLES)


def test_with_optional_parameters(ctx):
    schema = build_schema(
        ctx,
        [
            {"in": "query", "name": "q1", "schema": {"type": "string"}, "required": True, "example": "A1"},
            {"in": "query", "name": "q2", "schema": {"type": "integer"}, "required": False, "example": 10},
            {"in": "query", "name": "q3", "schema": {"type": "integer"}, "required": False, "example": 15},
            {"in": "query", "name": "q4", "schema": {"type": "integer"}, "required": False, "example": 20},
        ],
    )
    assert_positive_coverage(
        schema,
        [
            {
                "query": {
                    "q1": "A1",
                    "q2": "10",
                    "q3": "15",
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q4": "20",
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q3": "15",
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": "10",
                },
            },
            {
                "query": {
                    "q1": "A1",
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": "10",
                    "q3": "15",
                    "q4": "20",
                },
            },
        ],
    )


def test_with_example_openapi_3(ctx):
    schema = build_schema(
        ctx,
        [
            {"in": "query", "name": "q1", "schema": {"type": "string"}, "required": True, "example": "A1"},
            {"in": "query", "name": "q2", "schema": {"type": "integer"}, "required": True, "example": 10},
        ],
    )
    assert_positive_coverage(
        schema,
        [
            {
                "query": {
                    "q1": "A1",
                    "q2": "10",
                },
            },
        ],
    )


def test_with_response_example_openapi_3(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/items/{itemId}/": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "schema": {"type": "string"}, "required": True}],
                    "responses": {
                        "200": {
                            "description": "",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"},
                                    "examples": {
                                        "Example1": {"value": {"id": "123456"}},
                                        "Example2": {"value": {"itemId": "456789"}},
                                    },
                                }
                            },
                        }
                    },
                }
            }
        },
        components={"schemas": {"Item": {"properties": {"id": {"type": "string"}}}}},
    )
    assert_positive_coverage(
        schema,
        [
            {
                "path_parameters": {
                    "itemId": "456789",
                },
            },
            {
                "path_parameters": {
                    "itemId": "123456",
                },
            },
        ],
        path=("/items/{itemId}/", "get"),
    )


def test_with_examples_openapi_3_1(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "schema": {"type": "string", "examples": ["A1", "B2"]},
                            "required": True,
                        },
                        {
                            "in": "query",
                            "name": "q2",
                            "schema": {"type": "integer", "examples": [10, 20]},
                            "required": True,
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    assert_positive_coverage(schema, EXPECTED_EXAMPLES)


def test_with_examples_openapi_3_request_body(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "address": {
                                "type": "object",
                                "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
                            },
                        },
                        "required": ["name", "age"],
                    },
                    "examples": {
                        "example1": {
                            "value": {
                                "name": "John Doe",
                                "age": 30,
                                "tags": ["developer", "python"],
                                "address": {"street": "123 Main St", "city": "Anytown"},
                            }
                        },
                        "example2": {
                            "value": {
                                "name": "Jane Smith",
                                "age": 25,
                                "tags": ["designer", "ui/ux"],
                                "address": {"street": "456 Elm St", "city": "Somewhere"},
                            }
                        },
                    },
                }
            },
            "required": True,
        },
    )
    assert_positive_coverage(
        schema,
        [
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "123 Main St", "city": "Somewhere"},
                }
            },
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "456 Elm St", "city": "Anytown"},
                }
            },
            {"body": {"name": "John Doe", "age": 30, "tags": ["developer", "python"], "address": {}}},
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "123 Main St"},
                }
            },
            {"body": {"name": "John Doe", "age": 30, "tags": ["developer", "python"], "address": {"city": "Anytown"}}},
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "456 Elm St", "city": "Somewhere"},
                }
            },
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": [""],
                    "address": {"street": "123 Main St", "city": "Anytown"},
                }
            },
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["designer", "ui/ux"],
                    "address": {"street": "123 Main St", "city": "Anytown"},
                }
            },
            {
                "body": {
                    "name": "John Doe",
                    "age": 25,
                    "tags": ["developer", "python"],
                    "address": {"street": "123 Main St", "city": "Anytown"},
                }
            },
            {
                "body": {
                    "name": "Jane Smith",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "123 Main St", "city": "Anytown"},
                }
            },
            {"body": {"name": "John Doe", "age": 30}},
            {"body": {"name": "John Doe", "age": 30, "tags": ["developer", "python"]}},
            {"body": {"name": "John Doe", "age": 30, "address": {"street": "123 Main St", "city": "Anytown"}}},
            {
                "body": {
                    "name": "Jane Smith",
                    "age": 25,
                    "tags": ["designer", "ui/ux"],
                    "address": {"street": "456 Elm St", "city": "Somewhere"},
                }
            },
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "123 Main St", "city": "Anytown"},
                }
            },
        ],
    )


@pytest.mark.parametrize(
    ["first", "second"],
    [
        (
            {
                "first": {"value": "A1"},
                "second": {"value": "B2"},
            },
            {
                "first": {"value": 10},
                "second": {"value": 20},
            },
        ),
        (
            ["A1", "B2"],
            [10, 20],
        ),
    ],
)
def test_with_examples_openapi_2(ctx, first, second):
    schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "q1",
                "type": "string",
                "required": True,
                "x-examples": first,
            },
            {
                "in": "query",
                "name": "q2",
                "type": "integer",
                "required": True,
                "x-examples": second,
            },
        ],
        version="2.0",
    )
    assert_positive_coverage(schema, EXPECTED_EXAMPLES)


def test_property_example_wrong_type_is_not_used(ctx):
    # Schema where 'tags' declares type=string but its example is an array.
    # The coverage phase must not use the invalid example as a const; it should
    # fall back to generating a valid string so that every positive case passes
    # schema validation.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tags": {"type": "string", "example": ["tag1", "tag2"]},
            },
            "required": ["name"],
        },
        positive=True,
    )


def test_top_level_examples_list_filters_invalid_items(ctx):
    # When the body schema itself has an `examples` list with mixed valid/invalid items,
    # invalid items must be filtered and valid ones still yielded.
    # Exercises _positive_number directly (body is integer, not a property within object).
    collect_coverage_cases(
        ctx,
        {"type": "integer", "examples": ["not_a_number", 42]},
        positive=True,
    )


def test_default_wrong_type_is_not_used(ctx):
    # `default` annotations that violate the property's own type must be filtered.
    # `name` provides a valid example to anchor assembly; `count` has an invalid default only.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "example": "Alice"},
                "count": {"type": "integer", "default": "not_a_number"},
            },
            "required": ["name"],
        },
        positive=True,
    )


def test_mixed_type_keyword(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "key",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["a", "b"],
                        "additionalProperties": False,
                    },
                },
            },
        ],
    )
    assert_negative_coverage(
        schema,
        [
            {
                "query": {"key": ["0", "0"]},
            },
            {
                "query": {"key": [ANY]},
            },
            {
                "query": {"key": [["null", "null"]]},
            },
            {
                "query": {"key": ["null"]},
            },
            {
                "query": {"key": ["false"]},
            },
            {
                "query": {"key": ["0"]},
            },
            {
                "query": {"key": ""},
            },
            {
                "query": {"key": "null"},
            },
            {
                "query": {"key": "false"},
            },
        ],
    )


def test_negative_patterns(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "minLength": 3,
                                "maxLength": 10,
                                "pattern": "^[a-zA-Z0-9-_]$",
                            },
                        },
                        "required": ["name"],
                    },
                }
            },
            "required": True,
        },
    )
    assert_negative_coverage(
        schema,
        [
            {
                "body": {},
            },
            {
                "body": {
                    "name": "000",
                },
            },
            {
                "body": {
                    "name": "00000000000",
                },
            },
            {
                "body": {
                    "name": "00",
                },
            },
            {
                "body": {
                    "name": {},
                },
            },
            {
                "body": {
                    "name": [None, None],
                },
            },
            {
                "body": {
                    "name": None,
                },
            },
            {
                "body": {
                    "name": False,
                },
            },
            {
                "body": {
                    "name": 0,
                },
            },
            {
                "body": [None, None],
            },
            {
                "body": "",
            },
            {},
            {
                "body": False,
            },
            {
                "body": 0,
            },
        ],
    )


def test_query_parameters_always_negative():
    # See GH-2900
    schema = {
        "openapi": "3.0.3",
        "paths": {
            "/password": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "charset",
                            "required": False,
                            "schema": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 256,
                                "pattern": "^[!\"#$%&'()*+,\\-./0-9:;<=>?@A-Z\\[\\\\\\]^_`a-z{|}~]+$",
                            },
                            "example": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
                        },
                        {
                            "in": "query",
                            "name": "length",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 32},
                            "example": 16,
                        },
                        {
                            "in": "query",
                            "name": "quantity",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 10},
                            "example": 2,
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
    }

    assert_negative_coverage(schema, ANY, ("/password", "get"))


def test_array_in_header_path_query(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "X-API-Key-1", "in": "header", "required": True, "schema": {"type": "number"}},
            {"name": "key", "in": "query", "required": True, "schema": {"type": "number"}},
            {"name": "bar", "in": "path", "required": True, "schema": {"type": "number"}},
        ],
        path="/foo/{bar}",
    )
    assert_negative_coverage(
        schema,
        [
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "false"},
            },
            {
                "path_parameters": {"bar": "false"},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "false"},
                "query": {"key": ["false", "false"]},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "false"},
                "query": {"key": ["null", "null"]},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "false"},
                "query": {"key": ""},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "false"},
                "query": {"key": "null"},
            },
            {
                "headers": {"X-API-Key-1": "{}"},
                "path_parameters": {"bar": "false"},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": "null,null"},
                "path_parameters": {"bar": "false"},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": ""},
                "path_parameters": {"bar": "false"},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": "null"},
                "path_parameters": {"bar": "false"},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "null%2Cnull"},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": Pattern(".")},
                "query": {"key": "false"},
            },
            {
                "headers": {"X-API-Key-1": "false"},
                "path_parameters": {"bar": "null"},
                "query": {"key": "false"},
            },
        ],
        path=("/foo/{bar}", "post"),
    )


def test_required_header_as_string(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "X-API-Key-1", "in": "header", "required": True, "schema": {"type": "string"}},
            {"name": "X-API-Key-2", "in": "header", "required": True, "schema": {"type": "string"}},
        ],
    )
    # Header is a string and we can't generate anything positive, except for a test case with missing headers
    assert_negative_coverage(schema, [{}])


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"const": 42},
    ],
)
def test_underspecified_path_parameters(ctx, cli, snapshot_cli, openapi3_base_url, schema):
    # There should be no "Path parameter 'organization_id' is not defined"
    schema_path = ctx.openapi.write_schema(
        {
            "/organizations/{organization_id}/": {
                "get": {
                    "parameters": [
                        {
                            "name": "organization_id",
                            "in": "path",
                            "required": True,
                            "schema": schema,
                        }
                    ],
                    "responses": {"200": {"description": "Successful Response"}},
                }
            }
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--phases=coverage",
        )
        == snapshot_cli
    )


def test_path_parameters_arent_missing(ctx, cli, snapshot_cli, openapi3_base_url):
    # When `--mode=negative`, still generate path parameters if they can't be negated
    schema_path = ctx.openapi.write_schema(
        {
            "/organizations/{organization_id}/": {
                "get": {
                    "parameters": [
                        {
                            "name": "organization_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 10},
                        },
                    ],
                    "responses": {"200": {"description": "Successful Response"}},
                }
            }
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--checks=not_a_server_error",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


@pytest.mark.filterwarnings("error")
def test_path_parameters_without_schema(ctx, cli, snapshot_cli, openapi2_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/{param}": {
                "put": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "param",
                            "x-custom": 0,
                        }
                    ],
                }
            }
        },
        version="2.0",
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi2_base_url}",
            "--checks=not_a_server_error",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


def test_path_parameter_dots(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "number", "pattern": "[^.]"},
            }
        ],
    )
    assert_negative_coverage(
        schema,
        (
            [
                {"path_parameters": {"name": "%2E"}},
                {"path_parameters": {"name": "null%2Cnull"}},
                {"path_parameters": {"name": ANY}},
                {"path_parameters": {"name": "null"}},
            ],
            [
                {"path_parameters": {"name": "null%2Cnull"}},
                {"path_parameters": {"name": "%2E"}},
                {"path_parameters": {"name": ANY}},
            ],
            [
                {"path_parameters": {"name": "null%2Cnull"}},
                {"path_parameters": {"name": ANY}},
                {"path_parameters": {"name": "null"}},
            ],
            [
                {"path_parameters": {"name": "%2E%2E"}},
                {"path_parameters": {"name": "null%2Cnull"}},
                {"path_parameters": {"name": ANY}},
                {"path_parameters": {"name": "null"}},
            ],
            [
                {"path_parameters": {"name": "%2E"}},
                {"path_parameters": {"name": "null%2Cnull"}},
                {"path_parameters": {"name": ANY}},
            ],
            [
                {"path_parameters": {"name": "null%2Cnull"}},
                {"path_parameters": {"name": "null"}},
            ],
        ),
    )


def test_required_header(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "X-API-Key-1", "in": "header", "required": True, "schema": {"type": "string", "maxLength": 5}},
            {"name": "X-API-Key-2", "in": "header", "required": True, "schema": {"type": "string", "maxLength": 5}},
        ],
    )
    assert_negative_coverage(
        schema,
        [
            {
                "headers": {"X-API-Key-1": Pattern(".{5,}")},
            },
            {
                "headers": {"X-API-Key-2": Pattern(".{5,}")},
            },
            {
                "headers": {"X-API-Key-1": Pattern(".{5,}"), "X-API-Key-2": Pattern(".{5,}")},
            },
            {
                "headers": {"X-API-Key-1": Pattern(".{5,}"), "X-API-Key-2": Pattern(".{5,}")},
            },
            {
                "headers": {"X-API-Key-1": Pattern(".{5,}"), "X-API-Key-2": Pattern(".{5,}")},
            },
            {
                "headers": {"X-API-Key-1": Pattern(".{5,}"), "X-API-Key-2": Pattern(".{5,}")},
            },
        ],
    )


def test_required_and_optional_headers_only_type(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "X-API-Key-1", "in": "header", "required": True, "schema": {"type": "string"}},
            {"name": "X-API-Key-2", "in": "header", "schema": {"type": "string"}},
        ],
    )
    assert_negative_coverage(
        schema,
        [
            # Can't really negate a parameter that can be anything, except for make it missing and injecting an unknown one
            {
                "headers": {"x-schemathesis-unknown-property": "42"},
            },
            {},
        ],
    )


def test_required_and_optional_headers(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "X-API-Key-1",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^[0-9]{5}$"},
            },
            {"name": "X-API-Key-2", "in": "header", "schema": {"type": "string", "pattern": "^[0-9]{5}$"}},
        ],
    )
    assert_negative_coverage(
        schema,
        [
            {
                "headers": {"X-API-Key-1": "00000", "x-schemathesis-unknown-property": "42"},
            },
            {
                "headers": {"X-API-Key-1": ""},
            },
            {
                "headers": {"X-API-Key-1": "{}"},
            },
            {
                "headers": {"X-API-Key-1": "null,null"},
            },
            {
                "headers": {"X-API-Key-1": "null"},
            },
            {
                "headers": {"X-API-Key-1": "false"},
            },
            {
                "headers": {"X-API-Key-1": "0"},
            },
            {
                "headers": {"X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": ""},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "{}"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "null,null"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "null"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "false"},
            },
            {
                "headers": {"X-API-Key-1": "", "X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "{}", "X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "null,null", "X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "null", "X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "false", "X-API-Key-2": "0"},
            },
        ],
    )


def test_path_parameter_string_non_empty(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
    )
    assert_positive_coverage(schema, [{"path_parameters": {"name": "0"}}])


@pytest.mark.parametrize("extra", [{}, {"pattern": "[0-9]{1}", "minLength": 1}])
def test_path_parameter_invalid_example(ctx, extra):
    schema = build_schema(
        ctx,
        [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "string", **extra},
                "example": "/",
            }
        ],
    )
    assert_positive_coverage(schema, [{"path_parameters": {"name": "0"}}])


def test_path_parameter_as_string(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        path="/foo/{id}",
    )
    # Path parameter is a string and we can't generate anything positive
    assert_negative_coverage(
        schema,
        [],
        path=("/foo/{id}", "post"),
    )


def test_path_parameter(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string", "maxLength": 5}},
        ],
        path="/foo/{id}",
    )
    assert_negative_coverage(
        schema,
        [
            {
                "path_parameters": {
                    "id": "000000",
                },
            },
            {
                "path_parameters": {"id": Pattern(".{5,}")},
            },
        ],
        path=("/foo/{id}", "post"),
    )


def test_path_parameter_as_string_non_empty(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string", "minLength": 1}},
        ],
        path="/foo/{id}",
    )
    assert_coverage(
        schema,
        list(GenerationMode),
        [
            {
                "path_parameters": {
                    "id": "00",
                },
            },
            {
                "path_parameters": {
                    "id": "0",
                },
            },
        ],
        path=("/foo/{id}", "post"),
    )


def test_path_parameter_preserves_min_length(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "uid",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "minLength": 5, "maxLength": 64, "pattern": "^[0-9.]*$"},
            },
        ],
        path="/foo/{uid}",
    )
    assert_positive_coverage(
        schema,
        [
            {"path_parameters": {"uid": "0" * 63}},
            {"path_parameters": {"uid": "0" * 64}},
            {"path_parameters": {"uid": "0" * 6}},
            {"path_parameters": {"uid": "0" * 5}},
        ],
        path=("/foo/{uid}", "post"),
    )


def test_incorrect_headers_with_loose_schema(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "authorization",
                "in": "header",
                "required": False,
                "schema": {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Authorization"},
            }
        ],
    )
    assert_positive_coverage(
        schema,
        (
            [
                {"headers": {"authorization": ANY}},
                {"headers": {"authorization": "null"}},
                {"headers": {"authorization": ""}},
            ],
            [
                {"headers": {"authorization": "null"}},
                {"headers": {"authorization": ""}},
            ],
        ),
    )


def test_incorrect_headers(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "X-API-Key-1",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "example": "тест",
            },
        ],
    )
    assert_positive_coverage(schema, [{"headers": {"X-API-Key-1": ""}}])


def test_use_default(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "Key",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "default": "DEFAULT-VALUE"},
            },
        ],
    )
    assert_positive_coverage(schema, [{"query": {"Key": "DEFAULT-VALUE"}}])


def test_optional_parameter_without_type(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "query",
                "required": True,
                "schema": {"title": "Query", "type": "string"},
            },
            {
                "in": "query",
                "name": "locking_period",
                "required": False,
                "schema": {"default": 24, "title": "Locking Period"},
            },
        ],
    )
    assert_negative_coverage(
        schema,
        [
            # Can't really negate a parameter that can be anything, except for make it missing and injecting an unknown one
            {
                "query": {
                    "x-schemathesis-unknown-property": "42",
                },
            },
            {},
        ],
    )


def test_incorrect_headers_with_enum(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "X-API-Key-1",
                "in": "header",
                "required": True,
                "schema": {"enum": ["foo"]},
            },
        ],
    )
    assert_negative_coverage(
        schema,
        (
            [
                {},
                {"headers": {"X-API-Key-1": "{}"}},
                {"headers": {"X-API-Key-1": "null,null"}},
                {"headers": {"X-API-Key-1": "null"}},
                {"headers": {"X-API-Key-1": "false"}},
                {"headers": {"X-API-Key-1": "0"}},
            ],
            [
                {},
                {"headers": {"X-API-Key-1": "{}"}},
                {"headers": {"X-API-Key-1": "null,null"}},
                {"headers": {"X-API-Key-1": "false"}},
                {"headers": {"X-API-Key-1": "0"}},
            ],
            [
                {},
                {"headers": {"X-API-Key-1": "{}"}},
                {"headers": {"X-API-Key-1": "null,null"}},
                {"headers": {"X-API-Key-1": "null"}},
                {"headers": {"X-API-Key-1": "0"}},
            ],
            [
                {},
                {"headers": {"X-API-Key-1": "{}"}},
                {"headers": {"X-API-Key-1": "null,null"}},
                {"headers": {"X-API-Key-1": "null"}},
                {"headers": {"X-API-Key-1": "false"}},
            ],
        ),
    )


def test_generate_empty_headers_too(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "X-API-Key-1",
                "in": "header",
                "required": True,
                "schema": {
                    "maxLength": 40,
                    "pattern": "^[\\w\\W]+$",
                    "type": "string",
                },
            },
        ],
    )
    assert_negative_coverage(
        schema,
        [
            {},
            {"headers": {"X-API-Key-1": ""}},
        ],
    )


@pytest.mark.parametrize(
    ["schema", "expected"],
    [
        (
            {
                "type": "array",
                "items": {"type": "boolean"},
                "maxItems": 3,
            },
            [
                {"body": [False, False, False, False]},
                {"body": [{}]},
                {"body": [[None, None]]},
                {"body": [""]},
                {"body": [None]},
                {"body": [0]},
                {"body": {}},
                {"body": ""},
                {},
                {"body": False},
                {"body": 0},
            ],
        ),
        (
            {
                "type": "array",
                "items": {"type": "boolean"},
                "minItems": 3,
            },
            [
                {"body": [False, False]},
                {"body": [{}]},
                {"body": [[None, None]]},
                {"body": [""]},
                {"body": [None]},
                {"body": [0]},
                {"body": {}},
                {"body": ""},
                {},
                {"body": False},
                {"body": 0},
            ],
        ),
        (
            {
                "type": "array",
                "items": {
                    # Use an untranslatable PCRE pattern to test unsupported regex handling
                    "pattern": "[\\p{Greek}]+",
                },
                "maxItems": 50,
            },
            [
                {
                    "body": [None] * 51,
                },
                {
                    "body": {},
                },
                {
                    "body": "",
                },
                {},
                {
                    "body": False,
                },
                {
                    "body": 0,
                },
            ],
        ),
    ],
)
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_array_constraints(ctx, schema, expected):
    schema = build_schema(ctx, request_body={"required": True, "content": {"application/json": {"schema": schema}}})
    assert_negative_coverage(schema, expected)


def test_string_with_format(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "in": "path",
                "name": "foo_id",
                "schema": {"type": "string", "format": "uuid"},
                "required": True,
            },
        ],
        path="/foo/{foo_id}",
    )

    schema = schemathesis.openapi.from_dict(schema)

    def test(case):
        uuid.UUID(case.path_parameters["foo_id"], version=4)

    run_positive_test(schema["/foo/{foo_id}"]["post"], test)


def test_query_parameters_with_nested_enum(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "q1",
                "schema": {
                    "items": {
                        "enum": [
                            "A",
                            "B",
                            "C",
                            "D",
                            "E",
                            "F",
                        ],
                        "type": "string",
                    },
                    "type": "array",
                },
                "required": True,
            },
        ],
    )
    assert_positive_coverage(
        schema,
        [
            {
                "query": {
                    "q1": [
                        "F",
                    ],
                },
            },
            {
                "query": {
                    "q1": [
                        "E",
                    ],
                },
            },
            {
                "query": {
                    "q1": [
                        "D",
                    ],
                },
            },
            {
                "query": {
                    "q1": [
                        "C",
                    ],
                },
            },
            {
                "query": {
                    "q1": [
                        "B",
                    ],
                },
            },
            {
                "query": {
                    "q1": [
                        "A",
                    ],
                },
            },
            {
                "query": {
                    "q1": [],
                },
            },
        ],
    )


def test_query_parameters_dont_exceed_max_length(ctx):
    schema = build_schema(
        ctx,
        [
            {
                "name": "foo",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "string",
                    "pattern": "^bar\\.spam\\.[^,]+(?:,bar\\.spam\\.[^,]+)*$",
                    "minLength": 1,
                    "maxLength": 60,
                },
            },
        ],
    )
    assert_positive_coverage(
        schema,
        [
            {
                "query": {
                    "foo": "bar.spam.000000,bar.spam.0,bar.spam.0,bar.spam.0,bar.spam.0",
                },
            },
            {
                "query": {
                    "foo": "bar.spam.0000000,bar.spam.0,bar.spam.0,bar.spam.0,bar.spam.0",
                },
            },
            {
                "query": {
                    "foo": "bar.spam.0",
                },
            },
        ],
    )


def foo_id(value):
    return {
        "path_parameters": {
            "foo_id": value,
        },
    }


@pytest.mark.parametrize(
    ["schema", "expected"],
    [
        (
            {
                "type": "integer",
            },
            (
                [
                    foo_id("null%2Cnull"),
                    foo_id(Pattern(".")),
                    foo_id("null"),
                    foo_id("false"),
                ],
                [
                    foo_id("null%2Cnull"),
                    foo_id(Pattern(".")),
                    foo_id("false"),
                ],
            ),
        ),
        (
            {"type": "string", "format": "date-time"},
            [
                foo_id("0"),
                foo_id("null%2Cnull"),
                foo_id("null"),
                foo_id("false"),
            ],
        ),
    ],
)
def test_path_parameters_always_present(ctx, schema, expected):
    schema = build_schema(
        ctx,
        [
            {
                "name": "foo_id",
                "in": "path",
                "required": True,
                "schema": schema,
            },
        ],
        path="/foo/{foo_id}",
    )
    assert_negative_coverage(
        schema,
        expected,
        ("/foo/{foo_id}", "post"),
    )


def test_path_parameters_without_constraints_negative(ctx):
    # When there are no constraints, then we can't generate negative values as everything will match the previous schema
    schema = build_schema(
        ctx,
        [
            {
                "name": "foo_id",
                "in": "path",
                "required": True,
                "schema": {},
            },
        ],
        path="/foo/{foo_id}",
    )
    assert_negative_coverage(
        schema,
        [],
        ("/foo/{foo_id}", "post"),
    )


def test_path_parameters_with_unsupported_regex_pattern(ctx):
    # Use an untranslatable PCRE pattern to test unsupported regex handling
    schema = build_schema(
        ctx,
        [
            {
                "name": "foo_id",
                "in": "path",
                "required": True,
                "schema": {"pattern": "'^[-._\\p{Greek}]+$'"},
            },
        ],
        path="/foo/{foo_id}",
    )
    assert_negative_coverage(
        schema,
        [],
        ("/foo/{foo_id}", "post"),
    )


def test_query_without_constraints_negative(ctx):
    # When there are no constraints, then we can't generate negative values as everything will match the previous schema, only missing parameter
    schema = build_schema(
        ctx,
        [
            {
                "name": "q",
                "in": "query",
                "required": True,
                "schema": {},
            },
        ],
    )
    assert_negative_coverage(schema, [{}])


@pytest.mark.parametrize(
    ["schema", "required", "expected"],
    [
        [
            {
                "type": "string",
                "enum": ["foo", "bar", "spam"],
                "example": "spam",
            },
            False,
            [
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo?q=AAA",
                "http://127.0.0.1/foo?q=null&q=null",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
            ],
        ],
        [
            {"type": "array", "items": {"type": "string"}},
            False,
            [
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
            ],
        ],
        [
            {"type": "array", "items": {"type": "string", "pattern": "^[0-9]{3,5}$"}},
            False,
            [
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null&q=null",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
                "http://127.0.0.1/foo?q=0",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
            ],
        ],
        [
            {"type": "array", "items": {"type": "string", "pattern": "^[0-9]{3,5}$"}},
            True,
            [
                "http://127.0.0.1/foo",
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null&q=null",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
                "http://127.0.0.1/foo?q=0",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
            ],
        ],
    ],
)
def test_negative_query_parameter(ctx, schema, expected, required):
    schema = build_schema(
        ctx,
        [
            {
                "name": "q",
                "in": "query",
                "required": required,
                "schema": schema,
            }
        ],
    )

    schema = schemathesis.openapi.from_dict(schema)

    urls = []
    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        kwargs = case.as_transport_kwargs(base_url="http://127.0.0.1")
        request = Request(**kwargs).prepare()
        if not required:
            # We generate negative data - optional parameters should appear in the URL, but should be incorrect
            # Having it absent makes the case positive
            assert "?q=" in request.url
        urls.append(request.url)

    run_negative_test(operation, test, generate_duplicate_query_parameters=True)

    assert urls == expected


def test_negative_data_rejection(ctx, cli, openapi3_base_url, snapshot_cli):
    raw_schema = build_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "page_num",
                "required": False,
                "schema": {"type": "integer", "minimum": 1, "maximum": 999, "default": 1},
            }
        ],
        path="/success",
        method="get",
    )
    schema_path = ctx.openapi.write_schema(raw_schema["paths"])
    assert (
        cli.main(
            "run",
            str(schema_path),
            "-c",
            "negative_data_rejection",
            f"--url={openapi3_base_url}",
            "--mode=all",
            "--max-examples=10",
            "--phases=coverage",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ["required", "properties"],
    (
        (["key"], None),
        (["key"], {"another": {"type": "string"}}),
        (["key", "description"], {"key": {"type": "string"}}),
    ),
)
def test_request_body_is_required(ctx, required, properties):
    inner = {
        "additionalProperties": False,
        "required": required,
        "type": "object",
    }
    if properties is not None:
        inner["properties"] = properties
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "parameters": [
                        {"in": "query", "name": "strict", "schema": {}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {"data": inner},
                                    "type": "object",
                                }
                            }
                        },
                        "required": True,
                    },
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)

    operation = schema["/items"]["post"]

    def test(case):
        # Body is `required`, hence should never be unset for positive tests
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


@pytest.mark.parametrize("required", [["name"], ["name", "description"]])
def test_request_body_with_references(ctx, required):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {"data": {"$ref": "#/components/schemas/Item"}},
                                    "required": ["data"],
                                    "type": "object",
                                }
                            }
                        },
                        "required": True,
                    }
                }
            }
        },
        components={
            "schemas": {
                "Name": {"type": "string"},
                "Item": {
                    "additionalProperties": False,
                    "properties": {"name": {"$ref": "#/components/schemas/Name"}},
                    "required": required,
                    "type": "object",
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)

    operation = schema["/items"]["post"]

    def test(case):
        # Body is `required`, hence should never be unset for positive tests
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


def test_request_body_without_validation_keywords(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"x-something": True}}},
                        "required": True,
                    }
                }
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)

    operation = schema["/items"]["post"]

    def test(case):
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


@pytest.mark.openapi_version("3.0")
def test_unspecified_http_methods(ctx, cli, openapi3_base_url, snapshot_cli):
    raw_schema = {
        "/foo": {
            "post": {
                "parameters": [{"in": "query", "name": "key", "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            },
            "get": {
                "responses": {"200": {"description": "OK"}},
            },
        }
    }
    schema = ctx.openapi.build_schema(raw_schema)

    schema = schemathesis.openapi.from_dict(schema)

    methods = set()
    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario != CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        methods.add(case.method)
        assert f"-X {case.method}" in case.as_curl_command()

    run_negative_test(operation, test)

    assert methods == {"PATCH", "TRACE", "DELETE", "OPTIONS", "PUT", "QUERY"}

    methods = set()

    run_negative_test(operation, test, unexpected_methods={"DELETE", "PUT"})

    assert methods == {"DELETE", "PUT"}

    schema_path = ctx.openapi.write_schema(raw_schema)
    with ctx.check(
        """
import schemathesis

@schemathesis.check
def failed(ctx, response, case):
    if case.meta and getattr(case.meta.phase.data, "description", "") == "Unspecified HTTP method: DELETE":
        raise AssertionError(f"Should be {case.meta.phase.data.description}")
"""
    ) as module:
        assert (
            cli.main(
                "run",
                str(schema_path),
                "-c",
                "failed,unsupported_method",
                "--include-method=POST",
                f"--url={openapi3_base_url}",
                "--mode=negative",
                "--max-examples=10",
                "--continue-on-failure",
                hooks=module,
            )
            == snapshot_cli
        )


@pytest.mark.openapi_version("3.0")
def test_avoid_testing_unexpected_methods(ctx):
    raw_schema = {
        "/foo": {
            "post": {
                "parameters": [{"in": "query", "name": "key", "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            },
            "get": {
                "responses": {"200": {"description": "OK"}},
            },
        }
    }
    schema = ctx.openapi.build_schema(raw_schema)

    schema = schemathesis.openapi.from_dict(schema)

    methods = set()
    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario != CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        methods.add(case.method)
        assert f"-X {case.method}" in case.as_curl_command()

    run_negative_test(operation, test, unexpected_methods=set())

    assert not methods


@pytest.mark.openapi_version("3.0")
def test_avoid_testing_unexpected_methods_in_cli(ctx, cli, snapshot_cli, openapi3_base_url):
    raw_schema = {
        "/foo": {
            "post": {
                "parameters": [{"in": "query", "name": "key", "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            },
            "get": {
                "responses": {"200": {"description": "OK"}},
            },
        }
    }
    schema_path = ctx.openapi.write_schema(raw_schema)

    assert (
        cli.main(
            "run",
            str(schema_path),
            "--checks=unsupported_method",
            f"--url={openapi3_base_url}",
            "--phases=coverage",
            "--mode=negative",
            config={
                "phases": {
                    "coverage": {
                        "unexpected-methods": [],
                    }
                },
            },
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
def test_coverage_failure_shows_actual_method_in_header(ctx, cli, snapshot_cli, openapi3_base_url):
    # Regression test for GH-3322
    # When coverage phase tests unexpected HTTP methods (e.g., PATCH on a GET endpoint),
    # the failure header should show the actual tested method, not the original endpoint's method
    raw_schema = {
        "/resource": {
            "get": {"responses": {"200": {"description": "OK"}}},
        }
    }
    schema_path = ctx.openapi.write_schema(raw_schema)

    assert (
        cli.main(
            "run",
            str(schema_path),
            "--checks=unsupported_method",
            f"--url={openapi3_base_url}",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


def test_missing_authorization(ctx, cli, snapshot_cli, openapi3_base_url):
    # The reproduction code should not contain auth if it is explicitly specified
    schema_path = ctx.openapi.write_schema(
        {"/failure": {"get": {"security": [{"ApiKeyAuth": None}]}}},
        version="2.0",
        securityDefinitions={"ApiKeyAuth": {"type": "apiKey", "name": "Authorization", "in": "header"}},
    )
    assert (
        cli.main(
            "run",
            str(schema_path),
            "-c",
            "not_a_server_error",
            f"--url={openapi3_base_url}",
            "--header=Authorization: Bearer SECRET",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


def test_unnecessary_auth_warning(ctx, cli, snapshot_cli, openapi3_base_url):
    # If a test for missing Authorization is the only thing that happen, there should be no warning for missing Authorization header
    schema_path = ctx.openapi.write_schema(
        {
            "/basic": {
                "get": {
                    "security": [{"Basic": None}],
                    "responses": {
                        "200": {
                            "description": "Ok",
                        }
                    },
                }
            }
        },
        version="2.0",
        securityDefinitions={"Basic": {"type": "basic", "name": "Authorization", "in": "header"}},
    )
    assert (
        cli.main(
            "run",
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--header=Authorization: Basic dGVzdDp0ZXN0",
            "--max-examples=5",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
def test_nested_parameters(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "range",
                            "in": "query",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "null"},
                                },
                            },
                        }
                    ]
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    ranges = set()
    operation = schema["/test"]["get"]

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario != CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        ranges.add(case.query["range"])

    run_negative_test(operation, test)

    assert ranges == {"0"}


def _request_body(inner):
    return {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": inner,
                }
            }
        }
    }


@pytest.mark.parametrize(
    ["operation", "components"],
    [
        (
            _request_body(
                {
                    "properties": {
                        "p1": {
                            "$ref": "#components/schemas/Key",
                        }
                    }
                }
            ),
            {
                "schemas": {
                    "Key": {
                        "allOf": [
                            {"$ref": ""},
                        ]
                    }
                }
            },
        ),
        (
            _request_body({"$ref": "#components/schemas/Key"}),
            {
                "schemas": {
                    "Key": {
                        "default": 0,
                        "items": {
                            "$ref": "",
                        },
                    }
                }
            },
        ),
        (
            {"parameters": [{"$ref": "#components/parameters/q"}]},
            {
                "parameters": {
                    "q": {
                        "in": "header",
                        "name": "q",
                        "content": {
                            "text/plain": {"schema": {"$ref": "#unknown"}},
                        },
                    }
                }
            },
        ),
    ],
    ids=["body-combinator", "body-items", "parameter-unresolvable"],
)
def test_references(ctx, operation, components):
    raw_schema = ctx.openapi.build_schema({"/test": {"post": operation}}, components=components)
    schema = schemathesis.openapi.from_dict(raw_schema)
    for operation in schema.get_all_operations():
        if isinstance(operation, Ok):
            for _ in _iter_coverage_cases(
                operation=operation.ok(),
                generation_modes=list(GenerationMode),
                generate_duplicate_query_parameters=False,
                unexpected_methods=set(),
                generation_config=schema.config.generation,
            ):
                pass
        else:
            assert "Unresolvable reference in the schema" in str(operation.err())


def test_urlencoded_payloads_are_valid(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "number", "example": 1},
                        },
                        "required": ["key"],
                    },
                    "example": {"key": 1},
                }
            },
        },
    )
    schema = schemathesis.openapi.from_dict(schema)

    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase != TestPhase.COVERAGE:
            return
        assert_requests_call(case)

    run_test(operation, test)


def test_malformed_content_type(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "invalid": {
                    "schema": {"type": "object"},
                }
            },
        },
    )
    schema = schemathesis.openapi.from_dict(schema)

    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase != TestPhase.COVERAGE:
            return
        assert_requests_call(case)

    with pytest.raises(InvalidSchema):
        run_test(operation, test)


def test_no_missing_header_duplication(ctx):
    schema = build_schema(
        ctx,
        [
            {"name": "X-Key-1", "in": "header", "required": False, "schema": {"type": "string"}},
            {"name": "X-Key-2", "in": "header", "required": False, "schema": {"type": "string"}},
            {"name": "X-Key-3", "in": "header", "required": True, "schema": {"type": "string"}},
        ],
    )
    schema = schemathesis.openapi.from_dict(schema)

    descriptions = []
    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        descriptions.append(case.meta.phase.data.description)

    run_test(operation, test)

    assert "Missing required property: X-Key-3" not in descriptions
    assert "Missing `X-Key-3` at header" in descriptions


def assert_coverage(schema, modes, expected, path=None):
    schema = schemathesis.openapi.from_dict(schema)
    schema.config.phases.coverage.generate_duplicate_query_parameters = True

    cases = []
    operation = schema[path[0]][path[1]] if path else schema["/foo"]["post"]

    def test(case):
        meta = case.meta
        if meta.phase.name != TestPhase.COVERAGE:
            return
        if meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        assert_requests_call(case)
        mode = meta.generation.mode
        if len(modes) == 1:
            assert mode == modes[0]
        else:
            if mode == GenerationMode.POSITIVE:
                # If the main mode is positive, then all components should have the positive mode
                for component, info in case.meta.components.items():
                    assert info.mode == mode, f"{component.value} should have {mode.value} mode"
            if mode == GenerationMode.NEGATIVE:
                # If the main mode is negative, then at least one component should be negative
                assert any(info.mode == mode for info in case.meta.components.values())
        if (
            mode == GenerationMode.NEGATIVE
            and meta.phase.data.parameter_location
            in [
                "query",
                "path",
                "header",
                "cookie",
            ]
            and not (
                meta.phase.data.scenario == CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES
                and meta.phase.data.parameter is None
            )
        ):
            _validate_negative_parameter_serialization(case)

        if meta.phase.data.scenario == CoverageScenario.MAXIMUM_LENGTH_STRING:
            value, parameter = get_value_and_parameter(case)
            assert len(value) == parameter.definition["schema"]["maxLength"]

        output = {}
        for container in LOCATION_TO_CONTAINER.values():
            value = getattr(case, container)
            if container != "body" and not value:
                continue
            if value is not None and value is not NOT_SET:
                output[container] = value
        cases.append(output)

    run_test(operation, test, modes=modes, generate_duplicate_query_parameters=True)

    if isinstance(expected, tuple):
        assert cases in expected
    else:
        assert cases == expected


def get_value_and_parameter(case):
    location = LOCATION_TO_CONTAINER[case.meta.phase.data.parameter_location]
    name = case.meta.phase.data.parameter
    container = getattr(case, location)
    parameter = getattr(case.operation, location).get(name)
    return container.get(name), parameter


def _validate_negative_parameter_serialization(case):
    """Validate that negative test cases remain negative after HTTP serialization."""
    # This addresses the false positive issue where generated non-string values
    # (like `null`, `false`, `123`) become valid strings after HTTP serialization
    # (like `"null"`, `"false"`, `"123"`), causing "API accepted schema-violating request" errors.
    #
    # For example:
    # - Generated: charset=None (Python None)
    # - Serialized: charset=null (string "null")
    # - If "null" matches the string pattern, it's actually valid, not negative
    #
    value, parameter = get_value_and_parameter(case)

    # Get the serialized values that will actually be sent to the API
    data = case.meta.phase.data
    if data.scenario == CoverageScenario.MISSING_PARAMETER and parameter.definition.get("required"):
        # Missing required parameter - proper negative test case
        return
    if data.scenario == CoverageScenario.DUPLICATE_PARAMETER:
        # Duplicate parameter is negative not in the schema sense
        return
    serialized_items = _get_serialized_parameter_values(value, data.parameter, data.parameter_location)

    # Validate each serialized value against the parameter schema
    _validate_serialized_items_are_negative(serialized_items, parameter, case)


def _get_serialized_parameter_values(value, parameter_name, location):
    """Get the actual serialized values that will be sent to the API."""
    if location == "query":
        return _serialize_query_parameter(value, parameter_name)
    elif location == "path":
        return [unquote(str(value))]
    return [str(value)]


def _serialize_query_parameter(value, parameter_name):
    """Serialize a query parameter."""
    encoded = RequestEncodingMixin._encode_params({parameter_name: value})
    if encoded == f"{parameter_name}=":
        # Empty value case: param=
        return [""]
    elif not encoded:
        # No parameter sent (None/empty case)
        return []
    return parse_qs(encoded).get(parameter_name, [])


def _validate_serialized_items_are_negative(serialized_items, parameter, case):
    """Validate that serialized parameter values are actually negative."""
    # If a serialized value passes validation, it means we generated a "negative"
    # test case that's actually positive after serialization - this is a false positive.
    if not serialized_items:
        # Empty items list - this is only negative if parameter is required
        if not parameter.definition.get("required", False):
            pytest.fail(
                f"Generated empty parameter '{parameter.name}' but parameter is not required. "
                f"This creates a false positive in negative testing."
            )
        return

    # Get the JSON schema for validation
    schema = parameter.optimized_schema
    validator = case.operation.schema.adapter.jsonschema_validator_cls(schema)

    # Check each serialized value
    for item in serialized_items:
        try:
            validator.validate(item)
            # If validation passes, this is a false positive
            pytest.fail(
                f"FALSE POSITIVE: Generated negative value became valid after serialization.\n"
                f"Parameter: {parameter.name}\n"
                f"Serialized value: '{item}'\n"
                f"Schema: {schema}\n"
                f"Description: {case.meta.phase.data.description}\n"
                f"This value should be invalid but passes validation after HTTP serialization."
            )
        except jsonschema_rs.ValidationError:
            # Validation failed - this is expected for negative cases
            pass


def test_binary_format_should_not_generate_empty_string_as_invalid(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/files/{filename}": {
                "put": {
                    "parameters": [{"in": "path", "name": "filename", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/octet-stream": {
                                "schema": {
                                    "type": "string",
                                    "format": "binary",
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Created"},
                        "400": {"description": "Bad Request"},
                    },
                }
            }
        }
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/files/<path:filename>", methods=["PUT"])
    def upload_file(filename):
        data = request.get_data()
        return jsonify({"message": "File added successfully", "size": len(data)}), 201

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c",
            "negative_data_rejection",
            "--mode=negative",
            "--max-examples=50",
            "--phases=coverage",
        )
        == snapshot_cli
    )


def test_negative_type_violation_for_const_property(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "actions": {
                                            "type": "array",
                                            "items": {
                                                "anyOf": [
                                                    {"$ref": "#/components/schemas/DoNothing"},
                                                    {"$ref": "#/components/schemas/CallWebhook"},
                                                ]
                                            },
                                        }
                                    },
                                    "required": ["actions"],
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
                "DoNothing": {
                    "type": "object",
                    "properties": {
                        "type": {"const": "do-nothing", "type": "string"},
                    },
                },
                "CallWebhook": {
                    "type": "object",
                    "properties": {
                        "block_document_id": {"format": "uuid", "type": "string"},
                        "type": {"const": "call-webhook", "type": "string"},
                    },
                    "required": ["block_document_id"],
                },
            }
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/test"]["POST"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_negative_test(operation, collect)

    # Should generate type violations (non-string) for the `type` property
    type_violations = [
        c
        for c in cases
        if isinstance(c.body, dict)
        and isinstance(c.body.get("actions"), list)
        and len(c.body["actions"]) == 1
        and isinstance(c.body["actions"][0], dict)
        and "type" in c.body["actions"][0]
        and not isinstance(c.body["actions"][0]["type"], str)
    ]
    assert len(type_violations) > 0, (
        f"Should generate type violations (non-string) for type property. "
        f"Got bodies: {[c.body for c in cases if isinstance(c.body, dict) and c.body.get('actions')]}"
    )


def test_additional_properties_with_schema_positive(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    }
                }
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_positive_test(operation, collect)

    # Should generate objects with string values
    with_string_values = [
        c for c in cases if isinstance(c.body, dict) and any(isinstance(v, str) for v in c.body.values())
    ]
    assert len(with_string_values) > 0, (
        f"Should generate objects with string values. Got bodies: {[c.body for c in cases]}"
    )


def test_additional_properties_with_schema_negative(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    }
                }
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_negative_test(operation, collect)

    # Should generate objects with non-string values (type violations)
    with_invalid_values = [
        c for c in cases if isinstance(c.body, dict) and any(not isinstance(v, str) for v in c.body.values())
    ]
    assert len(with_invalid_values) > 0, (
        f"Should generate objects with non-string values. Got bodies: {[c.body for c in cases]}"
    )


def test_additional_properties_anyof_positive(ctx):
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ]
                        },
                    }
                }
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_positive_test(operation, collect)

    # Should generate both string values and array values
    with_string = [c for c in cases if isinstance(c.body, dict) and any(isinstance(v, str) for v in c.body.values())]
    with_array = [c for c in cases if isinstance(c.body, dict) and any(isinstance(v, list) for v in c.body.values())]
    assert len(with_string) > 0, f"Should generate objects with string values. Got bodies: {[c.body for c in cases]}"
    assert len(with_array) > 0, f"Should generate objects with array values. Got bodies: {[c.body for c in cases]}"


def test_max_properties_negative(ctx):
    cases = collect_coverage_cases(
        ctx, {"type": "object", "maxProperties": 2, "additionalProperties": {"type": "string"}}
    )
    exceeding = [c for c in cases if isinstance(c.body, dict) and len(c.body) > 2]
    assert len(exceeding) > 0, f"Should generate objects exceeding maxProperties. Got bodies: {[c.body for c in cases]}"


def test_min_properties_negative(ctx):
    cases = collect_coverage_cases(
        ctx, {"type": "object", "minProperties": 2, "additionalProperties": {"type": "string"}}
    )
    below = [c for c in cases if isinstance(c.body, dict) and len(c.body) < 2]
    assert len(below) > 0, f"Should generate objects below minProperties. Got bodies: {[c.body for c in cases]}"


def test_max_properties_with_additional_properties_false(ctx):
    cases = collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "maxProperties": 2,
            "additionalProperties": False,
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        },
    )
    exceeding = [c for c in cases if c.meta.phase.data.scenario == CoverageScenario.OBJECT_ABOVE_MAX_PROPERTIES]
    assert len(exceeding) == 0, (
        f"Should NOT generate OBJECT_ABOVE_MAX_PROPERTIES when additionalProperties: false. Got: {exceeding}"
    )


def test_max_properties_zero(ctx):
    cases = collect_coverage_cases(
        ctx, {"type": "object", "maxProperties": 0, "additionalProperties": {"type": "string"}}
    )
    exceeding = [c for c in cases if isinstance(c.body, dict) and len(c.body) > 0]
    assert len(exceeding) > 0, (
        f"Should generate objects with at least 1 property. Got bodies: {[c.body for c in cases]}"
    )


def test_min_properties_with_required(ctx):
    cases = collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "minProperties": 2,
            "required": ["a", "b"],
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        },
    )
    below = [c for c in cases if c.meta.phase.data.scenario == CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES]
    assert len(below) == 0, (
        f"Should NOT generate OBJECT_BELOW_MIN_PROPERTIES when required >= minProperties. Got: {below}"
    )


def test_max_properties_default_additional_properties(ctx):
    cases = collect_coverage_cases(ctx, {"type": "object", "maxProperties": 1})
    exceeding = [c for c in cases if isinstance(c.body, dict) and len(c.body) > 1]
    assert len(exceeding) > 0, (
        f"Should generate objects exceeding maxProperties with default additionalProperties. Got bodies: {[c.body for c in cases]}"
    )


def test_min_properties_one(ctx):
    cases = collect_coverage_cases(ctx, {"type": "object", "minProperties": 1})
    empty = [c for c in cases if c.meta.phase.data.scenario == CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES]
    assert len(empty) > 0, (
        f"Should generate OBJECT_BELOW_MIN_PROPERTIES for minProperties: 1. Got: {[c.body for c in cases]}"
    )
    assert any(c.body == {} for c in empty), (
        f"Should generate empty object for minProperties: 1. Got: {[c.body for c in empty]}"
    )


def test_min_properties_fewer_than_required(ctx):
    cases = collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "minProperties": 1,
            "required": ["a", "b", "c"],
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "c": {"type": "string"}},
        },
    )
    below = [c for c in cases if c.meta.phase.data.scenario == CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES]
    assert len(below) == 0, (
        f"Should NOT generate OBJECT_BELOW_MIN_PROPERTIES when required > minProperties. Got: {below}"
    )


def test_missing_content_type_header(ctx):
    # Regression: "missing Content-Type header" test case should not include Content-Type in request
    schema = build_schema(
        ctx,
        parameters=[
            {"in": "header", "name": "Content-Type", "schema": {"type": "string"}, "required": True},
        ],
        request_body={
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]

    missing_content_type_case = None

    def find_case(case):
        nonlocal missing_content_type_case
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        phase_data = case.meta.phase.data
        if phase_data.scenario == CoverageScenario.MISSING_PARAMETER and phase_data.parameter.lower() == "content-type":
            missing_content_type_case = case

    run_negative_test(operation, find_case)

    assert missing_content_type_case is not None, "Should generate missing Content-Type case"

    kwargs = missing_content_type_case.as_transport_kwargs(base_url="http://127.0.0.1")
    request = Request(**kwargs).prepare()
    assert "Content-Type" not in request.headers, (
        f"Missing Content-Type test should not have Content-Type header, got: {dict(request.headers)}"
    )


def test_path_parameter_with_slash_in_custom_format(ctx):
    # See GH-3527
    schemathesis.openapi.format("ipv4-network", st.sampled_from(["0.0.0.0/0"]))
    schema = build_schema(
        ctx,
        path="/blocks/{block}",
        method="get",
        parameters=[
            {
                "name": "block",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "format": "ipv4-network"},
            }
        ],
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/blocks/{block}"]["get"]

    path_values = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            path_values.append(case.path_parameters.get("block"))

    run_positive_test(operation, collect)

    assert path_values, "No coverage cases generated"
    assert all(v == "0.0.0.0%2F0" for v in path_values), f"Unexpected values: {path_values}"


def _collect_xml_coverage_cases(ctx, body_schema):
    """Build an XML-only schema, run in negative mode, and return coverage phase cases."""
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {"application/xml": {"schema": body_schema}},
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_negative_test(operation, collect)
    return cases


def test_xml_string_field_no_type_mutations(ctx):
    # For {"type": "string"} XML fields, type mutations produce the same wire bytes as valid strings.
    # None -> "", False -> "False", 0 -> "0" all become valid string content in XML elements.
    cases = _collect_xml_coverage_cases(
        ctx,
        {"type": "object", "properties": {"x-prop": {"type": "string"}}, "required": ["x-prop"]},
    )
    type_mutation_bodies = [
        c.body
        for c in cases
        if isinstance(c.body, dict) and "x-prop" in c.body and not isinstance(c.body["x-prop"], str)
    ]
    assert type_mutation_bodies == [], (
        f"No type mutations should be generated for XML string fields, got: {type_mutation_bodies}"
    )


def test_xml_constrained_string_field_generates_violations(ctx):
    # Constrained string schemas (e.g. minLength) should produce violations in negative mode.
    cases = _collect_xml_coverage_cases(
        ctx,
        {"type": "object", "properties": {"x-prop": {"type": "string", "minLength": 5}}, "required": ["x-prop"]},
    )
    violation_bodies = [
        c.body
        for c in cases
        if isinstance(c.body, dict) and isinstance(c.body.get("x-prop"), str) and len(c.body["x-prop"]) < 5
    ]
    assert violation_bodies, "Constrained XML string fields should generate constraint violations"


def test_xml_object_body_no_ambiguous_mutations(ctx):
    # For XML object bodies, both null and empty string serialize to <RootTag></RootTag>,
    # which is identical to an empty object {} at the wire level. Neither should be generated.
    cases = _collect_xml_coverage_cases(
        ctx,
        {"type": "object", "properties": {"x-prop": {"type": "string"}}},
    )
    ambiguous = [c for c in cases if c.body is None or c.body == ""]
    assert ambiguous == [], (
        f"Null/empty-string body mutations should not be generated for XML object bodies, got: {ambiguous}"
    )


def test_xml_none_property_mutation_filtered_when_schema_accepts_empty_string(ctx):
    # For XML string fields, _escape_xml(None) = "" (not "None").
    # Schema {"type": "string", "maxLength": 0} accepts only "" — None should NOT be generated
    # because it produces the same valid wire content.
    cases = _collect_xml_coverage_cases(
        ctx,
        {"type": "object", "properties": {"x-prop": {"type": "string", "maxLength": 0}}, "required": ["x-prop"]},
    )
    null_property_mutations = [
        c for c in cases if isinstance(c.body, dict) and "x-prop" in c.body and c.body["x-prop"] is None
    ]
    assert null_property_mutations == [], (
        f"None mutation for XML string field with maxLength:0 should be filtered, got: {null_property_mutations}"
    )


def test_query_method_appears_in_unspecified_methods(ctx):
    schema = ctx.openapi.build_schema(
        {"/search": {"post": {"responses": {"200": {"description": "OK"}}}}},
        version="3.2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/search"]["post"]

    methods = set()

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario != CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        methods.add(case.method)

    run_negative_test(operation, test)

    assert "QUERY" in methods


def test_query_method_excluded_from_unexpected_when_defined(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/search": {
                "query": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
        version="3.2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/search"]["post"]

    methods = set()

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario != CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        methods.add(case.method)

    run_negative_test(operation, test)

    assert "QUERY" not in methods


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_hostname_format_generation_and_validation_consistent(ctx, version):
    # See GH-3567: generated values should be validated with the same draft semantics.
    body_schema = {"type": "string", "format": "hostname"}
    assert collect_coverage_cases(ctx, body_schema, positive=True, version=version)
    assert collect_coverage_cases(ctx, body_schema, positive=False, version=version)


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_duration_format_generates_required_body_positive_cases(ctx, version):
    # Duration format should not eliminate all positive body values.
    body_schema = {"type": "string", "format": "duration"}
    assert collect_coverage_cases(ctx, body_schema, positive=True, version=version)


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_duration_format_generates_required_query_positive_cases(ctx, version):
    # Required query parameters should not be omitted for duration format.
    schema = build_schema(
        ctx,
        parameters=[
            {
                "name": "duration",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "format": "duration"},
            }
        ],
        version=version,
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]
    validator_cls = operation.schema.adapter.jsonschema_validator_cls
    validator = validator_cls({"type": "string", "format": "duration"}, validate_formats=True)
    cases = []

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        value = case.query.get("duration") if case.query else None
        assert value is not None
        assert validator.is_valid(value)
        cases.append(case)

    run_positive_test(operation, test)

    assert cases


@pytest.mark.parametrize(
    ("validator_cls", "should_generate"),
    [
        (jsonschema_rs.Draft4Validator, False),
        (jsonschema_rs.Draft202012Validator, True),
    ],
)
def test_hostname_negative_format_respects_validator_draft(monkeypatch, validator_cls, should_generate):
    # `XN--9krT00a` is valid in Draft 4 but invalid in Draft 2020-12.
    monkeypatch.setattr(coverage_generation, "from_schema", lambda *_args, **_kwargs: st.just("XN--9krT00a"))
    ctx = coverage_generation.CoverageContext(
        root_schema={"type": "string", "format": "hostname"},
        location=ParameterLocation.QUERY,
        media_type=None,
        generation_modes=[GenerationMode.NEGATIVE],
        is_required=True,
        custom_formats={},
        validator_cls=validator_cls,
    )

    generator = coverage_generation._negative_format(ctx, {"type": "string", "format": "hostname"}, "hostname")

    if should_generate:
        value = next(generator)
        assert value.value == "XN--9krT00a"
    else:
        with pytest.raises(Unsatisfiable):
            next(generator)


def test_missing_required_header_case_uses_invalid_template_body(ctx):
    # In NEGATIVE-only mode the template body is set from the first negative mutation
    # (e.g. `0`). MISSING_PARAMETER test cases inherit that invalid body, so a server
    # that validates body before header returns 422 and header validation is never reached
    # - a false negative for missing_required_header.
    body_schema = {
        "oneOf": [
            {"type": "null"},
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ]
    }
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {
                            "name": "X-Required-Header",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/test"]["post"]
    validator = operation.schema.adapter.jsonschema_validator_cls(body_schema, validate_formats=False)

    missing_header_cases = [
        case
        for case in _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
        if case.meta.phase.data.scenario == CoverageScenario.MISSING_PARAMETER
        and case.meta.phase.data.parameter == "X-Required-Header"
    ]

    assert missing_header_cases, "Expected at least one MISSING_PARAMETER case for X-Required-Header"
    # Template body must be valid so the server reaches header validation, not body rejection.
    assert all(validator.is_valid(case.body) for case in missing_header_cases), (
        f"Missing-header cases must have a valid body, got: {[case.body for case in missing_header_cases]}"
    )


def test_missing_required_header_case_respects_before_call_hook_restoring_header(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "put": {
                    "parameters": [
                        {
                            "name": "X-Required-Header",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["put"]

    missing_header_case = next(
        case
        for case in _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
        if case.meta.phase.data.scenario == CoverageScenario.MISSING_PARAMETER
        and case.meta.phase.data.parameter == "X-Required-Header"
    )

    assert missing_header_case.meta.generation.mode == GenerationMode.NEGATIVE

    missing_header_case.headers["X-Required-Header"] = "restored"

    assert missing_header_case.meta.generation.mode == GenerationMode.POSITIVE

    kwargs = missing_header_case.as_transport_kwargs(base_url="http://127.0.0.1")
    assert kwargs["headers"].get("X-Required-Header") == "restored"


def test_filter_case_hook_applied_in_coverage_phase(ctx):
    raw_schema = build_schema(
        ctx,
        parameters=[{"name": "key", "in": "query", "schema": {"type": "integer"}}],
        method="get",
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/foo"]["get"]

    # Verify some cases are produced without hook
    config = ProjectConfig()
    base_cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=config.phases.coverage.generate_duplicate_query_parameters,
            unexpected_methods=config.phases.coverage.unexpected_methods,
            generation_config=config.generation,
        )
    )
    assert base_cases, "Expected coverage cases before filtering"

    @loaded.hook
    def filter_case(context, case):
        return False  # reject everything

    filtered_cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=config.phases.coverage.generate_duplicate_query_parameters,
            unexpected_methods=config.phases.coverage.unexpected_methods,
            generation_config=config.generation,
        )
    )
    assert filtered_cases == [], "filter_case hook should suppress all coverage cases"


def test_map_case_hook_applied_in_coverage_phase(ctx):
    raw_schema = build_schema(
        ctx,
        parameters=[{"name": "key", "in": "query", "schema": {"type": "integer"}}],
        method="get",
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)

    @loaded.hook
    def map_case(context, case):
        if case.query is not None:
            case.query["injected"] = "yes"
        return case

    config = ProjectConfig()
    operation = loaded["/foo"]["get"]
    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=config.phases.coverage.generate_duplicate_query_parameters,
            unexpected_methods=config.phases.coverage.unexpected_methods,
            generation_config=config.generation,
        )
    )

    assert cases, "Expected at least one coverage case"
    assert all(c.query is None or c.query.get("injected") == "yes" for c in cases), (
        "map_case hook should have injected 'injected' into every query"
    )


def test_content_json_query_params_single_encoding_in_coverage(ctx):
    # See GH-3701
    schema = build_schema(
        ctx,
        parameters=[
            {
                "name": "filters",
                "in": "query",
                "required": True,
                "content": {"application/json": {"schema": {"type": "array", "example": []}}},
            },
        ],
        request_body={
            "required": True,
            "content": {"application/json": {"schema": {"type": "array", "items": {"type": "string"}}}},
        },
    )
    loaded = schemathesis.openapi.from_dict(schema)
    config = ProjectConfig()
    operation = loaded["/foo"]["post"]

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=config.phases.coverage.generate_duplicate_query_parameters,
            unexpected_methods=config.phases.coverage.unexpected_methods,
            generation_config=config.generation,
        )
    )

    assert len(cases) >= 2
    for case in cases:
        if case.query is None:
            continue
        raw = case.query.get("filters")
        if raw is None:
            continue
        assert isinstance(raw, str), f"Expected JSON string, got {type(raw).__name__}: {raw!r}"
        parsed = json.loads(raw)
        assert isinstance(parsed, list), "filters should decode to a list after single JSON encoding"


def test_coverage_body_with_boolean_property_key(ctx):
    # YAML parses bare `on:` as boolean True, so schemas loaded from YAML can have bool keys in `properties`.
    raw_schema = ctx.openapi.build_schema(
        {
            "/hooks": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        # True is a bool key - YAML artifact from bare `on:` field
                                        True: {"type": "string"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/hooks"]["POST"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0


def test_coverage_negative_max_length_preserved_in_optimized_schema(ctx):
    # When a pattern's outer '?' is rewritten to '{0,1}' without encoding maxLength
    # into the inner quantifiers, maxLength must survive in optimized_schema so the
    # conformance checker can flag over-long strings as schema-invalid.
    body_schema = {
        "type": "string",
        "maxLength": 10,
        "minLength": 0,
        "pattern": r"^(?:[A-Z0-9](?:[A-Z0-9][- ]?)*[A-Z0-9])?$",
    }
    raw_schema = ctx.openapi.build_schema(
        {
            "/zipcode": {
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
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/zipcode"]["post"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    assert "maxLength" in optimized_schema, f"maxLength must be preserved in optimized_schema; got: {optimized_schema}"

    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)
    max_length_cases = [
        case
        for case in _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
        if isinstance(case.body, str) and len(case.body) > 10
    ]
    assert max_length_cases, "Expected at least one NEGATIVE case with a body string longer than maxLength=10"
    for case in max_length_cases:
        assert not validator.is_valid(case.body), (
            f"NEGATIVE body longer than maxLength is schema-valid per optimized_schema: {case.body!r}"
        )


def test_coverage_positive_pattern_skipped_for_non_string_type(ctx):
    # When a schema has 'pattern' alongside a non-string 'type', the coverage
    # phase must not generate string values as POSITIVE cases — they violate 'type'
    # and are schema-invalid, causing false positive_data_acceptance failures.
    body_schema = {"type": "number", "pattern": "[0-9]{4}"}
    raw_schema = ctx.openapi.build_schema(
        {
            "/pin": {
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
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/pin"]["post"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    positive_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in positive_cases:
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid per optimized_schema: {case.body!r}"


def test_coverage_positive_allof_ref_property_merge(ctx):
    # Multi-level allOf chain (Child -> Intermediate -> Base) where Base defines 'location'.
    # canonicalish leaves an unresolved $ref inside the merged schema; cover_schema_iter must
    # deep-merge 'properties' from the resolved ref, not overwrite, so 'location' stays present.
    raw_schema = ctx.openapi.build_schema(
        {
            "/resources/{name}": {
                "put": {
                    "parameters": [
                        {"name": "name", "in": "path", "required": True, "type": "string"},
                        {"name": "body", "in": "body", "required": True, "schema": {"$ref": "#/definitions/Child"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
        definitions={
            "Base": {
                "properties": {
                    "location": {"type": "string"},
                    "id": {"type": "string", "readOnly": True},
                }
            },
            "Intermediate": {
                "allOf": [{"$ref": "#/definitions/Base"}],
                "properties": {"tags": {"type": "object", "additionalProperties": {"type": "string"}}},
                "required": ["location"],
            },
            "Child": {
                "allOf": [{"$ref": "#/definitions/Intermediate"}],
                "properties": {"extra": {"type": "string"}},
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/resources/{name}"]["put"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    positive_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in positive_cases:
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_body_with_boolean_property_key_negative(ctx):
    # YAML parses bare `on:` as boolean True, so schemas loaded from YAML can have bool keys in `properties`.
    raw_schema = ctx.openapi.build_schema(
        {
            "/hooks": {
                "post": {
                    "parameters": [
                        {
                            "name": "X-Hook-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        # True is a bool key - YAML artifact from bare `on:` field
                                        True: {"type": "string"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/hooks"]["POST"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0


def test_coverage_form_urlencoded_binary_format_negative(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "required": ["file", "name"],
                                    "properties": {
                                        "file": {"type": "string", "format": "binary"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/upload"]["POST"]

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        assert case.meta.phase.name == TestPhase.COVERAGE


def test_coverage_negative_empty_dict_additional_properties_not_treated_as_false(ctx):
    # `additionalProperties: {}` is equivalent to `true` — any extra property is valid.
    raw_schema = ctx.openapi.build_schema(
        {
            "/search": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "params": {
                                            "type": "object",
                                            "additionalProperties": {},
                                        },
                                        "query": {"type": "string"},
                                    },
                                    "required": ["query"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/search"]["POST"]
    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), (
            f"NEGATIVE body must be schema-invalid, got schema-valid body: {case.body!r}"
        )


def test_coverage_negative_pattern_with_control_chars_uses_schema_validator(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "pattern": r"^.{0,99}\S$",
                                            "minLength": 1,
                                            "maxLength": 100,
                                        }
                                    },
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]
    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        info = case.meta.components.get(ParameterLocation.BODY)
        if info is not None and info.mode == GenerationMode.NEGATIVE and case.body is not None:
            assert not validator.is_valid(case.body), (
                f"NEGATIVE body must be schema-invalid, got schema-valid body: {case.body!r}"
            )


def test_coverage_positive_body_uuid_format_with_uppercase_pattern(ctx):
    # A property schema with format:uuid AND a pattern that restricts to uppercase hex
    # must generate a POSITIVE value that is valid for BOTH constraints - i.e. an
    # uppercase UUID with hyphens.
    raw_schema = ctx.openapi.build_schema(
        {
            "/docs": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "templateId": {
                                            "type": "string",
                                            "format": "uuid",
                                            "pattern": "^[0-9A-F]{8}[-]?[0-9A-F]{4}[-]?[0-9A-F]{4}[-]?[0-9A-F]{4}[-]?[0-9A-F]{12}$",
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/docs"]["post"]
    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema, validate_formats=True)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        if case.body is not None:
            assert validator.is_valid(case.body), (
                f"POSITIVE body must be schema-valid, got schema-invalid body: {case.body!r}"
            )


def test_coverage_positive_body_skips_properties_with_no_valid_enum_values(ctx):
    # A property schema like {enum: ["MALE", "FEMALE"], maxLength: 1} has contradictory
    # constraints — all enum values violate maxLength. The coverage phase must not pick
    # an invalid enum value as the positive body template, causing POSITIVE body failures.
    raw_schema = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "gender": {
                                            "type": "string",
                                            "enum": ["MALE", "FEMALE", "UNKNOWN"],
                                            "maxLength": 1,
                                        },
                                    },
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["POST"]
    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        if case.body is not None:
            assert validator.is_valid(case.body), (
                f"POSITIVE body must be schema-valid, got schema-invalid body: {case.body!r}"
            )


def test_coverage_positive_object_type_with_items(ctx):
    # Schema property with type:"object" and "items" (a schema inconsistency) must not
    # cause generate_from_schema to produce a list — the items/type fast path must only
    # trigger for type:"array", not type:"object".
    raw_schema = ctx.openapi.build_schema(
        {
            "/register": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["value"],
                                    "properties": {
                                        "ids": {
                                            "type": "object",
                                            "items": {"type": "string"},
                                        },
                                        "value": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/register"]["post"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    positive_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in positive_cases:
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid per optimized_schema: {case.body!r}"


def test_coverage_negative_string_length_with_enum(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/submit": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["version"],
                                    "properties": {
                                        "version": {
                                            "type": "string",
                                            "enum": ["1.2", "1.3"],
                                            "minLength": 3,
                                            "maxLength": 3,
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/submit"]["post"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), f"NEGATIVE body is schema-valid: {case.body!r}"


def test_coverage_positive_template_with_enum_and_type_mismatch(ctx):
    # YAML parsing artifacts (e.g. bare `true`/`false`) in an enum with type:"string" must not
    # produce a schema-invalid template body.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "put": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "id",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["mode"],
                                    "properties": {
                                        "mode": {
                                            "type": "string",
                                            "enum": [True, False, "active"],
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items/{id}"]["put"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.POSITIVE:
            continue
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_template_required_property_absent_from_properties(ctx):
    # A required property not listed in `properties` must still appear in the template
    # body so the positive template is schema-valid when the negation is elsewhere.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "put": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "id",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["setting"],
                                    "properties": {
                                        "setting": {
                                            "required": ["name"],
                                            "properties": {
                                                "value": {"type": "string"},
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
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items/{id}"]["put"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.POSITIVE:
            continue
        assert validator.is_valid(case.body), f"POSITIVE template body is schema-invalid: {case.body!r}"


def test_coverage_positive_template_skips_false_schema_property(ctx):
    # A property with boolean `false` schema means no value is valid — skip it rather than
    # assigning `0`, which would make the POSITIVE body schema-invalid.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "patch": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "extra": False,
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items/{id}"]["patch"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.POSITIVE:
            continue
        assert validator.is_valid(case.body), f"POSITIVE template body is schema-invalid: {case.body!r}"


def test_coverage_negative_string_length_nullable(ctx):
    # STRING_ABOVE_MAX_LENGTH / STRING_BELOW_MIN_LENGTH must produce a string, not `None`,
    # when the schema has `type: ["string", "null"]`.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": ["string", "null"], "maxLength": 10}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), f"NEGATIVE body is schema-valid: {case.body!r}"


def test_coverage_negative_string_property_form_urlencoded_not_wire_identical(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"url": {"type": "string", "nullable": True}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]

    form_schema = next(
        alt.optimized_schema for alt in operation.body if alt.media_type == "application/x-www-form-urlencoded"
    )
    validator = jsonschema_rs.validator_for(form_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in cases:
        if case.media_type != "application/x-www-form-urlencoded":
            continue
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        if not isinstance(case.body, dict):
            continue
        # Simulate form-urlencoded: all values become strings on the wire
        stringified = {k: str(v) for k, v in case.body.items()}
        assert not validator.is_valid(stringified), (
            f"NEGATIVE body becomes schema-valid after form-urlencoded encoding: {case.body!r} → {stringified!r}"
        )


def test_coverage_negative_string_property_xml_not_wire_identical(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"url": {"type": "string", "nullable": True}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]

    xml_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/xml")
    validator = jsonschema_rs.validator_for(xml_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in cases:
        if case.media_type != "application/xml":
            continue
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        if not isinstance(case.body, dict):
            continue
        # Simulate XML encoding: primitives → str(v), empty dict/None → "" (empty element text content).
        # Lists and other complex values serialize differently (multiple elements) — skip those.
        for k, v in case.body.items():
            if isinstance(v, (bool, int, float)):
                wire = str(v)
                assert not validator.is_valid({**case.body, k: wire}), (
                    f"Property {k!r}: NEGATIVE body {case.body!r} becomes schema-valid after XML encoding (→ {wire!r})"
                )
            elif v == {} or v is None:
                assert not validator.is_valid({**case.body, k: ""}), (
                    f"Property {k!r}: NEGATIVE body {case.body!r} becomes schema-valid after XML encoding (→ '')"
                )


def test_coverage_positive_oneof_body_valid_for_whole_schema(ctx):
    # oneOf where both branches allow the same set of values (no additionalProperties: false).
    # POSITIVE coverage must not yield bodies that are invalid for the whole oneOf (i.e. valid
    # for multiple branches simultaneously).
    schema_dict = ctx.openapi.build_schema(
        {
            "/modify": {
                "patch": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {
                                            "type": "object",
                                            "properties": {"email": {"type": "string", "example": "a@b.com"}},
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "email": {"type": "string"},
                                                "code": {"type": "string"},
                                            },
                                        },
                                    ]
                                }
                            }
                        },
                    },
                    "responses": {"204": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/modify"]["PATCH"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid for oneOf: {case.body!r}"


def test_coverage_positive_body_ref_with_pattern_and_length_constraints(ctx):
    # POSITIVE bodies must satisfy the anchored pattern even when the object body uses
    # `additionalProperties: false` alongside `$ref` properties with pattern/length constraints.
    schema_dict = ctx.openapi.build_schema(
        {
            "/tasks": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TaskRequest"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "TaskRequest": {
                    "type": "object",
                    "required": ["TaskId"],
                    "properties": {"TaskId": {"$ref": "#/components/schemas/BatchLoadTaskId"}},
                    "additionalProperties": False,
                },
                "BatchLoadTaskId": {
                    "type": "string",
                    "pattern": "[A-Z0-9]+",
                    "minLength": 3,
                    "maxLength": 32,
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/tasks"]["POST"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_oneof_branch_required_field_missing_from_branch_properties(ctx):
    # POSITIVE bodies must satisfy the full schema when a oneOf branch requires a field
    # that is defined only in the parent schema's properties, not in the branch's own properties.
    schema_dict = ctx.openapi.build_schema(
        {
            "/runs": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {
                                            "additionalProperties": True,
                                            "properties": {"status": {"enum": ["completed"]}},
                                            "required": ["status", "conclusion"],
                                        },
                                        {
                                            "additionalProperties": True,
                                            "properties": {"status": {"enum": ["queued"]}},
                                        },
                                    ],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "head_sha": {"type": "string"},
                                        "status": {"enum": ["queued", "completed"], "type": "string"},
                                        "conclusion": {
                                            "enum": ["success", "failure"],
                                            "type": "string",
                                        },
                                    },
                                    "required": ["name", "head_sha"],
                                    "type": "object",
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/runs"]["POST"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_negative_format_nullable(ctx):
    # INVALID_FORMAT must produce a non-null string when the schema has `type: ["string", "null"]`.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"email": {"type": ["string", "null"], "format": "email"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]

    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), f"NEGATIVE body is schema-valid: {case.body!r}"


def test_coverage_form_urlencoded_primitive_body_negative_no_crash(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/convert": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {"schema": {"type": "integer", "format": "int32"}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/convert"]["POST"]

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        case.as_curl_command()


def test_coverage_negative_string_above_max_length_invalid_when_pattern_quantifier_merged(ctx):
    # An unanchored quantifier like `{1,50}` doesn't prevent a 51-char string from passing
    # JSON Schema validation (partial match). The optimizer must anchor the pattern.
    schema_dict = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "pattern": "[^/:|\\x00-\\x1f]+",
                                            "minLength": 1,
                                            "maxLength": 50,
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/items"]["POST"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    above_max_cases = [
        case
        for case in cases
        if case.meta is not None
        and case.meta.phase.data is not None
        and case.meta.phase.data.scenario == CoverageScenario.STRING_ABOVE_MAX_LENGTH
        and case.media_type == "application/json"
    ]
    assert len(above_max_cases) > 0
    for case in above_max_cases:
        assert not validator.is_valid(case.body), f"NEGATIVE body must be schema-invalid: {case.body!r}"


def test_coverage_negative_max_length_preserved_when_pattern_has_inner_quantifier(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["namespace"],
                                    "properties": {
                                        "namespace": {
                                            "type": "string",
                                            "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
                                            "minLength": 1,
                                            "maxLength": 63,
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), f"NEGATIVE body must be schema-invalid: {case.body!r}"


def test_coverage_negative_max_length_preserved_when_outer_optional_group_has_variable_inner(ctx):
    # Optional group with variable inner: minLength absorbed (? to {1}) but maxLength unrepresentable.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["key"],
                                    "properties": {
                                        "key": {
                                            "type": "string",
                                            "pattern": r"^([a-zA-Z0-9!_.*'()-][/a-zA-Z0-9!_.*'()-]*)?$",
                                            "minLength": 1,
                                            "maxLength": 5,
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), f"NEGATIVE body must be schema-invalid: {case.body!r}"


def test_coverage_negative_missing_required_with_additional_properties_schema(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string"},
                                        "linkedServiceName": {"type": "object"},
                                    },
                                    "additionalProperties": {"type": "object"},
                                    "required": ["type", "linkedServiceName"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]
    body_schema = operation.body[0].optimized_schema
    validator = jsonschema_rs.validator_for(body_schema)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), f"NEGATIVE body must be schema-invalid: {case.body!r}"


def test_positive_object_example_with_invalid_format_not_yielded(ctx):
    # Schema-level example with a property value that violates format: date-time (missing timezone).
    # The invalid example must not appear as a POSITIVE coverage case.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "properties": {
                "entryDate": {"type": "string", "format": "date-time"},
            },
            "example": {"entryDate": "2017-01-01T00:00:00"},
        },
        positive=True,
    )


def test_coverage_positive_pattern_with_branch_group_not_corrupted(ctx):
    # A pattern like `([a-z0-9]|-[a-z0-9])*` contains alternation inside a quantified group.
    # POSITIVE values such as "a-project-name" must pass optimized_schema validation.
    schema_dict = ctx.openapi.build_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "name",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "pattern": "^[a-z0-9]([a-z0-9]|-[a-z0-9])*$",
                                "minLength": 1,
                                "maxLength": 100,
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/items"]["GET"]
    query_param = next(p for p in operation.query if p.name == "name")
    optimized = query_param.optimized_schema
    validator = jsonschema_rs.validator_for(optimized)

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    positive_cases = [c for c in cases if c.query and "name" in c.query]
    assert len(positive_cases) > 0
    for case in positive_cases:
        assert validator.is_valid(case.query["name"]), (
            f"POSITIVE value {case.query['name']!r} failed optimized_schema validation — "
            f"pattern was likely corrupted by update_quantifier"
        )


def test_coverage_positive_property_names_enum_respected(ctx):
    # propertyNames with an enum must constrain generated keys; x-schemathesis-additional violates it.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "propertyNames": {"enum": ["red", "blue"]},
            "additionalProperties": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
            },
        },
        positive=True,
        version="3.1.0",
    )


def test_negative_data_rejection_no_crash_with_large_dfa_pattern(ctx, response_factory):
    # \S{1,8192} exceeds jsonschema_rs's default DFA size limit; FANCY_REGEX_OPTIONS must be
    # passed when building the multi-element-array validator inside the check.
    raw_schema = ctx.openapi.build_schema(
        {
            "/configuration": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "configuration_token",
                            "required": True,
                            "schema": {"type": "string", "pattern": r"\S{1,8192}"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/configuration"]["GET"]

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    response = response_factory.requests(status_code=200)
    ctx_check = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    for case in cases:
        try:
            negative_data_rejection(ctx_check, response, case)
        except AcceptedNegativeData:
            pass


def test_negative_data_rejection_no_false_positive_for_nullable_binary_multipart(ctx, response_factory):
    # `nullable: true` on a binary field converts to anyOf[{string/binary}, {null}].
    # Negating the null branch generates type mutations (dict, int, bool, etc.) that get
    # serialized to strings in multipart (str({}) -> "{}"), making them valid for the binary
    # field. is_valid_for_others must account for wire serialization so these aren't yielded.
    raw_schema = ctx.openapi.build_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "required": ["data"],
                                    "properties": {
                                        "data": {
                                            "type": "string",
                                            "format": "binary",
                                            "nullable": True,
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "OK"},
                        "400": {"description": "Bad Request"},
                    },
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/upload"]["POST"]

    cases = list(
        generate_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            auth_storage=None,
            as_strategy_kwargs={},
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    response = response_factory.requests(status_code=200)
    ctx_check = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    for case in cases:
        body = case.body
        if not isinstance(body, dict) or "data" not in body:
            continue
        data_val = body["data"]
        if isinstance(data_val, (str, bytes)):
            continue
        # Non-string value for binary field: str(data_val) is a valid binary string in multipart,
        # so the API will accept it — negative_data_rejection must not fire (false positive).
        assert negative_data_rejection(ctx_check, response, case) is None, (
            f"False positive: body {body!r} with data={data_val!r} ({type(data_val).__name__}) "
            f"becomes a valid binary string after multipart serialization"
        )


def test_coverage_positive_body_nested_allof_inner_required_preserved(ctx):
    # Required fields from the second inner $ref (e.g. 'direction') must appear in POSITIVE bodies
    # when a oneOf branch resolves to allOf[{$ref: base}, {$ref: extension}].
    raw_schema = ctx.openapi.build_schema(
        {
            "/reports": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "discriminator": {"propertyName": "product"},
                                    "oneOf": [{"$ref": "#/components/schemas/SMS"}],
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
                "SMS": {
                    "allOf": [
                        {"$ref": "#/components/schemas/base_request"},
                        {"$ref": "#/components/schemas/sms_fields"},
                    ]
                },
                "base_request": {
                    "type": "object",
                    "properties": {
                        "product": {"type": "string"},
                        "account_id": {"type": "string"},
                    },
                    "required": ["product", "account_id"],
                },
                "sms_fields": {
                    "type": "object",
                    "properties": {
                        "product": {"type": "string"},
                        "account_id": {"type": "string"},
                        "direction": {"type": "string"},
                    },
                    "required": ["product", "account_id", "direction"],
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/reports"]["POST"]
    body_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(body_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_string_type_with_empty_properties(ctx):
    # A property with type:string and properties:{} must generate a string value, not {}.
    # The properties keyword is irrelevant when type is not object.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "put": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["content"],
                                    "properties": {
                                        "content": {"type": "string", "properties": {}},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items/{id}"]["put"]
    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in cases:
        if case.body is None or not case.meta:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_required_unsatisfiable_array_enum(ctx):
    # POSITIVE bodies must satisfy `required` even when a property's schema is unsatisfiable.
    # The query parameter gives the coverage phase something else to negate.
    raw_schema = ctx.openapi.build_schema(
        {
            "/clients": {
                "post": {
                    "parameters": [{"in": "query", "name": "version", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["clientName", "grantTypes"],
                                    "properties": {
                                        "clientName": {"type": "string"},
                                        "grantTypes": {
                                            "type": "array",
                                            "enum": ["authorization_code", "refresh_token"],
                                            "items": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/clients"]["post"]
    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    negative_cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.POSITIVE:
            continue
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_no_recursion_for_allof_with_unmergeable_anyof_property(ctx):
    # Coverage must not recurse infinitely when canonicalish cannot merge allOf entries
    # (e.g. two object schemas with overlapping anyOf properties) and returns allOf with no type.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "allOf": [
                                        {
                                            "type": "object",
                                            "required": ["count"],
                                            "properties": {
                                                "count": {
                                                    "anyOf": [{"const": None}, {"type": "integer", "minimum": 0}]
                                                },
                                                "name": {"type": "string"},
                                            },
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "count": {
                                                    "anyOf": [
                                                        {"const": None},
                                                        {"type": "integer", "minimum": 0, "maximum": 100},
                                                    ]
                                                },
                                                "value": {"type": "number"},
                                            },
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
        version="3.1.0",
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]
    # Must complete without RecursionError
    list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )


def test_coverage_positive_body_anyof_const_null_excluded_by_sibling_type(ctx):
    # When anyOf has a {const: null} branch but the sibling `type` constraint forbids null,
    # POSITIVE coverage must not yield null as a valid value for that property.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["count"],
                                    "properties": {
                                        "count": {
                                            "anyOf": [{"const": None}, {"type": "integer", "minimum": 0}],
                                            "type": "integer",
                                            "minimum": 0,
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
        version="3.1.0",
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]
    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in cases:
        if case.body is None or case.meta is None:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_nested_required_unsatisfiable_field(ctx):
    # When a nested required field has an unsatisfiable schema (e.g. pattern+format contradiction),
    # the parent template must not include the incomplete sub-object as a POSITIVE value.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["card"],
                                    "properties": {
                                        "card": {
                                            "type": "object",
                                            "required": ["name", "expiry"],
                                            "properties": {
                                                "name": {"type": "string"},
                                                "expiry": {
                                                    "type": "string",
                                                    "format": "date",
                                                    "pattern": "YYYY-MM",
                                                },
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
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]
    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )
    for case in cases:
        if case.body is None or case.meta is None:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_revalidation_preserves_negative_mode_for_format_violating_body(ctx):
    # A NEGATIVE body with a format-violating value ('' for a uuid field) must stay
    # NEGATIVE after body reassignment triggers _revalidate_metadata.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "iterationId": {
                                            "type": "string",
                                            "format": "uuid",
                                            "nullable": True,
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=loaded.config.generation,
        )
    )

    target = next(
        (
            case
            for case in cases
            if isinstance(case.body, dict)
            and case.body.get("iterationId") == ""
            and case.meta is not None
            and case.meta.components.get(ParameterLocation.BODY) is not None
            and case.meta.components[ParameterLocation.BODY].mode == GenerationMode.NEGATIVE
        ),
        None,
    )
    assert target is not None, "No NEGATIVE case with iterationId='' found"

    # Simulates what the engine does when auth or overrides reassign the body.
    target.body = target.body

    assert target.meta is not None
    assert target.meta.components[ParameterLocation.BODY].mode == GenerationMode.NEGATIVE


def test_coverage_form_urlencoded_filters_primitives_with_bundled_ref(ctx):
    # Every NEGATIVE form-urlencoded body must remain schema-invalid after string coercion.
    raw_schema = ctx.openapi.build_schema(
        {
            "/t": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "input": {
                                            "anyOf": [
                                                {
                                                    "oneOf": [
                                                        {"type": "string", "maxLength": 1000},
                                                        {
                                                            "type": "array",
                                                            "items": {"$ref": "#/components/schemas/Nested"},
                                                        },
                                                    ]
                                                },
                                                {"type": "null"},
                                            ]
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
        components={
            "schemas": {
                "Nested": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Nested"}},
                }
            }
        },
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/t"]["post"]
    optimized_schema = next(
        alt.optimized_schema for alt in operation.body if alt.media_type == "application/x-www-form-urlencoded"
    )
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    for case in _iter_coverage_cases(
        operation=operation,
        generation_modes=[GenerationMode.NEGATIVE],
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=loaded.config.generation,
    ):
        if case.media_type != "application/x-www-form-urlencoded" or not isinstance(case.body, dict):
            continue
        bi = case.meta.components.get(ParameterLocation.BODY) if case.meta else None
        if not bi or bi.mode != GenerationMode.NEGATIVE:
            continue
        wire = {k: str(v) for k, v in case.body.items()}
        assert not validator.is_valid(wire), (
            f"NEGATIVE form-urlencoded body becomes schema-valid after string coercion: {case.body!r} -> {wire!r}"
        )


def test_coverage_array_above_max_items_with_complex_items_schema(ctx):
    # Every NEGATIVE body must fail schema validation.
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "items": {
                                            "type": "array",
                                            "maxItems": 20,
                                            "items": {
                                                "oneOf": [
                                                    {
                                                        "allOf": [
                                                            {
                                                                "type": "object",
                                                                "required": ["type", "role", "content"],
                                                                "properties": {
                                                                    "role": {
                                                                        "type": "string",
                                                                        "enum": ["user", "assistant"],
                                                                    },
                                                                    "content": {
                                                                        "oneOf": [
                                                                            {"type": "string"},
                                                                            {"type": "array"},
                                                                        ]
                                                                    },
                                                                    "type": {
                                                                        "type": "string",
                                                                        "enum": ["message"],
                                                                    },
                                                                },
                                                            },
                                                            {"properties": {"type": {"const": "EasyInputMessage"}}},
                                                        ]
                                                    }
                                                ],
                                                "discriminator": {"propertyName": "type"},
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
    )
    loaded = schemathesis.openapi.from_dict(raw_schema)
    operation = loaded["/items"]["post"]
    optimized_schema = next(alt.optimized_schema for alt in operation.body if alt.media_type == "application/json")
    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)

    for case in _iter_coverage_cases(
        operation=operation,
        generation_modes=[GenerationMode.NEGATIVE],
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=loaded.config.generation,
    ):
        if case.body is None or case.meta is None:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.NEGATIVE:
            assert not validator.is_valid(case.body), (
                f"NEGATIVE body is schema-valid (mutation had no effect): {case.body!r}"
            )
