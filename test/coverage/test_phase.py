import re
import uuid
from dataclasses import dataclass
from unittest.mock import ANY
from urllib.parse import parse_qs, unquote

import jsonschema
import pytest
from flask import Flask, jsonify, request
from hypothesis import Phase, settings
from jsonschema import ValidationError
from requests import Request
from requests.models import RequestEncodingMixin

import schemathesis
from schemathesis.config._projects import ProjectConfig
from schemathesis.core import NOT_SET
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.parameters import LOCATION_TO_CONTAINER
from schemathesis.core.result import Ok
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import (
    HypothesisTestConfig,
    HypothesisTestMode,
    _iter_coverage_cases,
    create_test,
)
from schemathesis.generation.meta import CoverageScenario, TestPhase
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
    {"query": {"q1": ANY}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": ["0", "0"]}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": [ANY, ANY], "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "00"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": "4", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ["null", "null"], "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": "", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": "null", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": "false", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "0000"}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": ANY}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "null,null"}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "null"}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": "false"}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": ANY}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "6", "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "{}", "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "null,null", "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "", "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "null", "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "false", "h2": Pattern("-?[0-9]+")}, "body": 0},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"x-prop": {}}},
    {
        "query": {"q1": ANY, "q2": "0"},
        "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")},
        "body": {"x-prop": [None, None]},
    },
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"x-prop": None}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"x-prop": False}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"x-prop": 0}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": [None, None]},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": ""},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}},
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
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": {}}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": [None, None]}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": None}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": False}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": ""},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}},
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


def collect_coverage_cases(ctx, body_schema, positive=False):
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
    )
    loaded = schemathesis.openapi.from_dict(schema)
    operation = loaded["/foo"]["post"]

    validator = jsonschema.Draft202012Validator(body_schema)
    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            is_valid = validator.is_valid(case.body)
            if positive and not is_valid:
                errors = list(validator.iter_errors(case.body))
                pytest.fail(
                    f"Positive case produced invalid body.\n"
                    f"Body: {case.body}\n"
                    f"Schema: {body_schema}\n"
                    f"Errors: {[e.message for e in errors]}"
                )
            if not positive and is_valid:
                pytest.fail(
                    f"Negative case produced valid body (should be invalid).\n"
                    f"Body: {case.body}\n"
                    f"Schema: {body_schema}\n"
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
                    "q3": "15",
                    "q4": "20",
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": "10",
                    "q4": "20",
                },
            },
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
                    "pattern": "[\\p{L}]+",
                },
                "maxItems": 50,
            },
            [
                {
                    "body": [],
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
    schema = build_schema(
        ctx,
        [
            {
                "name": "foo_id",
                "in": "path",
                "required": True,
                "schema": {"pattern": "'^[-._\\p{L}\\p{N}]+$'"},
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

    assert methods == {"PATCH", "TRACE", "DELETE", "OPTIONS", "PUT"}

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
            assert str(operation.err()) == "Schema `` has a required reference to itself"


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

    with pytest.raises(MalformedMediaType):
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
    validator = case.operation.schema.adapter.jsonschema_validator_cls(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )

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
        except ValidationError:
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

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

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
