import json
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass
from unittest.mock import ANY

import jsonschema_rs
import pytest
from flask import jsonify, request
from hypothesis import strategies as st
from requests import Request

import schemathesis
from schemathesis.config import SanitizationConfig
from schemathesis.core import NOT_SET
from schemathesis.core.error_feedback.store import (
    ErrorFeedbackStore,
    Observation,
    ObservationKind,
    SizeBoundPayload,
)
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import AcceptedNegativeData
from schemathesis.core.parameters import LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CoverageScenario, TestPhase
from schemathesis.specs.openapi.checks import negative_data_rejection
from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases
from schemathesis.specs.openapi.coverage._wire import quote_path_parameter
from schemathesis.transport.prepare import prepare_request
from test.coverage.helpers import (
    assert_bodies,
    assert_coverage,
    assert_negative_coverage,
    assert_positive_coverage,
    body_mode,
    body_operation,
    body_validator,
    build_schema,
    collect_cases,
    collect_coverage_cases,
    generate_cases,
    iter_cases,
    load_schema,
    make_request_body,
    optimized_body_schema,
    run_negative_test,
    run_positive_test,
    run_test,
    scenario_cases,
)
from test.utils import assert_requests_call, check_context


class AnyNumber:
    def __eq__(self, value: object, /) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int | float))


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
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "5", "q2": "000"}, "body": {"x-prop": Pattern(".+")}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "5", "q2": "000"}, "body": {"x-prop": Pattern(".+")}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": "5", "q2": "000"}, "body": {"j-prop": 0}},
]
NEGATIVE_CASES = [
    {"query": {"q1": "0.5"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": ["0", "0"]}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": ["0.5", "0.5"], "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "00"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "4", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": ["null", "null"], "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "AAA", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "null", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "true", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "0000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "null,null"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "null"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "6", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "{}", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "null,null", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "AAA", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "null", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "true", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": [None, None]},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": True},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": 0.5},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": 0},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": {}}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": [None, None]}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": "AAA"}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": None}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": False}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": {"j-prop": AnyNumber()}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": [None, None]},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": "AAA"},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": False},
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}, "body": 0},
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
    {"query": {"q1": "AAA", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "null", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "true", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "0.5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "6", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "0000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "null,null"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "null"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "true"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "00"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "6", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "{}", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "null,null", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "AAA", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "null", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "true", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "0.5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "4", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": True},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0.5},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": "00"}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": "0"}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": {}}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": [None, None]}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": "AAA"}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": None}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": False}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": AnyNumber()}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": "AAA"},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": False},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
]


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
    schema = build_schema(
        ctx,
        parameters=[{"name": "itemId", "in": "path", "schema": {"type": "string"}, "required": True}],
        responses={
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
        path="/items/{itemId}/",
        method="get",
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
    schema = build_schema(
        ctx,
        parameters=[
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


@pytest.mark.parametrize(
    "body",
    [
        {"type": "array", "contains": {"type": "integer"}, "minContains": 5},
        {"type": "array", "minItems": 1, "contains": {"type": "integer"}, "minContains": 3},
        {
            "type": "array",
            "items": {"type": ["integer", "string"]},
            "minItems": 6,
            "maxItems": 6,
            "contains": {"type": "integer"},
            "minContains": 4,
        },
        {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 3,
            "contains": {"type": "integer"},
            "minContains": 2,
        },
        {"type": "array", "contains": {"type": "integer"}},
        {
            "type": "array",
            "items": {"type": ["integer", "string"]},
            "minItems": 6,
            "maxItems": 6,
            "contains": {"type": "integer"},
            "maxContains": 2,
        },
        {"type": "array", "minItems": 5, "maxItems": 5, "contains": {"type": "integer"}, "maxContains": 2},
        {
            "type": "array",
            "items": {"enum": [1, 2, "a", "b"]},
            "minItems": 4,
            "maxItems": 4,
            "contains": {"type": "integer"},
            "maxContains": 1,
        },
        {"type": "array", "items": {"type": "string"}, "contains": {"const": "contains-marker"}},
    ],
    ids=[
        "no-min-items",
        "min-items-below-min-contains",
        "at-max-items",
        "already-satisfied",
        "no-min-contains",
        "max-contains-mixed",
        "max-contains-no-items",
        "enum-items",
        "single-item-branch",
    ],
)
def test_positive_arrays_honor_contains(ctx, body):
    # A positive array must keep its `contains` match count within `minContains`/`maxContains`.
    collect_coverage_cases(ctx, body, positive=True, version="3.1.0")


@pytest.mark.parametrize(
    "body",
    [
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a"],
            "dependentRequired": {"a": ["b"]},
        },
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a"],
            "dependencies": {"a": ["b"]},
        },
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a"],
            "dependentSchemas": {"a": {"required": ["b"]}},
        },
    ],
    ids=["dependent-required", "dependencies", "dependent-schemas"],
)
def test_positive_objects_honor_dependencies(ctx, body):
    # A present property that triggers a dependency must not be emitted without its dependents.
    collect_coverage_cases(ctx, body, positive=True, version="3.1.0")


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
                "query": {"key": [["null", "null"]]},
            },
            {
                "query": {"key": ["0"]},
            },
            {
                "query": {"key": "AAA"},
            },
            {
                "query": {"key": "null"},
            },
            {
                "query": {"key": "true"},
            },
            {
                "query": {"key": "0.5"},
            },
        ],
    )


def test_negative_type_violations_for_enum_property_under_allof(ctx):
    # `allOf` canonicalisation drops `type` from `{type, enum}` properties; the engine must
    # still emit type-violation negatives for those properties in mixed-mode coverage.
    schema = build_schema(
        ctx,
        body={
            "allOf": [
                {"type": "object"},
                {
                    "type": "object",
                    "required": ["color"],
                    "properties": {
                        "color": {"type": "string", "enum": ["red", "blue"]},
                    },
                },
            ],
        },
    )
    assert_coverage(
        schema,
        [GenerationMode.POSITIVE, GenerationMode.NEGATIVE],
        [
            {"body": [None, None]},
            {"body": "AAA"},
            {},
            {"body": False},
            {"body": 0},
            {"body": {}},
            {"body": {"color": "AAA"}},
            {"body": {"color": {}}},
            {"body": {"color": [None, None]}},
            {"body": {"color": None}},
            {"body": {"color": False}},
            {"body": {"color": 0}},
            {"body": {"color": "blue"}},
            {"body": {"color": "red"}},
        ],
    )


def test_negative_per_property_emitted_when_inflated_template_unsatisfiable(ctx):
    # One unsatisfiable optional property must not silence per-property negatives on the others.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "format": {"enum": ["json", "xml"], "type": "string"},
                "unsat": {"type": "integer", "minimum": 10, "maximum": 5},
            },
        },
    )
    cases = iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
    enum_invalid = [
        c.body
        for c in cases
        if isinstance(c.body, dict)
        and "format" in c.body
        and c.body["format"] not in ("json", "xml")
        and c.meta.generation.mode == GenerationMode.NEGATIVE
    ]
    assert enum_invalid, (
        f"Expected a negative case with an invalid 'format' enum value; got bodies: {[c.body for c in cases]}"
    )


def test_positive_oneof_number_branch_covered_when_example_pins_string(ctx):
    # Spec example "5xx" satisfies the string branch but not the number branch; without a
    # baseline fallback the number branch yields no positive case and `/oneOf/0/type` ends
    # up as `needs_valid` even though the schema is satisfiable.
    schema = build_schema(
        ctx,
        [
            {
                "name": "statusCode",
                "in": "query",
                "required": False,
                "schema": {
                    "examples": ["5xx"],
                    "oneOf": [{"type": "number"}, {"type": "string"}],
                },
            },
        ],
    )
    assert_coverage(
        schema,
        [GenerationMode.POSITIVE],
        [
            {"query": {"statusCode": "5xx"}},
            {"query": {"statusCode": "0"}},
        ],
    )


def test_positive_oneof_query_array_and_string_both_reach_valid(ctx):
    # Without a non-empty bare string the wire form `?domain=` matches the array branch too,
    # so the string branch never reaches `valid` in tools that match by serialized form.
    operation = load_schema(
        ctx,
        [
            {
                "name": "domain",
                "in": "query",
                "required": False,
                "schema": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        {"type": "string"},
                    ],
                },
            },
        ],
        method="get",
    )["/foo"]["get"]
    values = [
        case.query.get("domain")
        for case in collect_cases(operation, GenerationMode.POSITIVE, generate_duplicate_query_parameters=False)
        if case.meta.phase.data.scenario != CoverageScenario.UNSPECIFIED_HTTP_METHOD
    ]

    has_non_empty_bare_string = any(isinstance(v, str) and v for v in values)
    has_array = any(isinstance(v, list) for v in values)
    assert has_non_empty_bare_string and has_array, (
        f"Each oneOf branch must yield at least one positive case; got {values!r}"
    )


def test_no_redundant_type_violations_for_enum_string_property_in_multipart(ctx):
    # Multipart stringifies every value, so non-strings for a string-typed property
    # collapse into the enum negation already emitted.
    schema = build_schema(
        ctx,
        body={
            "type": "object",
            "required": ["color"],
            "properties": {
                "color": {"type": "string", "enum": ["red", "blue"]},
            },
        },
        media_type="multipart/form-data",
    )
    assert_coverage(
        schema,
        [GenerationMode.POSITIVE, GenerationMode.NEGATIVE],
        [
            {"body": {"color": "AAA"}},
            {"body": {}},
            {"body": {"color": "blue"}},
            {"body": {"color": "red"}},
        ],
    )


def test_below_min_items_negative_emitted_when_array_schema_carries_examples(ctx):
    # Array schemas with `minItems > 0` and a sibling `examples` (or `example`/`default`)
    # must still emit an empty-array negative — generation used to short-circuit on the
    # spec-declared example and skip the constraint-violating shape.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Item"},
                    "minItems": 1,
                    "maxItems": 50,
                    "examples": [[{"id": "a"}]],
                },
            },
        },
        components={
            "schemas": {
                "Item": {"type": "object", "properties": {"id": {"type": "string"}}},
            },
        },
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)
    empty_array = [c for c in cases if isinstance(c.body, dict) and c.body.get("items") == []]
    assert empty_array and all(
        c.meta.phase.data.scenario == CoverageScenario.ARRAY_BELOW_MIN_ITEMS for c in empty_array
    ), [c.body for c in cases]


def test_negative_patterns(ctx):
    schema = build_schema(
        ctx,
        body={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 10,
                    "pattern": "^[a-zA-Z0-9-_]+$",
                },
            },
            "required": ["name"],
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
                    # Arbitrary text that does not match the pattern, drawn within the length bounds.
                    "name": Pattern("(?s)^.{3,10}$"),
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
                "body": "AAA",
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
            {"headers": {"X-API-Key-1": "true"}, "path_parameters": {"bar": "true"}},
            {"path_parameters": {"bar": "true"}, "query": {"key": "true"}},
            {
                "headers": {"X-API-Key-1": "true"},
                "path_parameters": {"bar": "true"},
                "query": {"key": ["true", "true"]},
            },
            {
                "headers": {"X-API-Key-1": "true"},
                "path_parameters": {"bar": "true"},
                "query": {"key": ["null", "null"]},
            },
            {"headers": {"X-API-Key-1": "true"}, "path_parameters": {"bar": "true"}, "query": {"key": "AAA"}},
            {"headers": {"X-API-Key-1": "true"}, "path_parameters": {"bar": "true"}, "query": {"key": "null"}},
            {"headers": {"X-API-Key-1": "{}"}, "path_parameters": {"bar": "true"}, "query": {"key": "true"}},
            {"headers": {"X-API-Key-1": "null,null"}, "path_parameters": {"bar": "true"}, "query": {"key": "true"}},
            {"headers": {"X-API-Key-1": "AAA"}, "path_parameters": {"bar": "true"}, "query": {"key": "true"}},
            {"headers": {"X-API-Key-1": "null"}, "path_parameters": {"bar": "true"}, "query": {"key": "true"}},
            {"headers": {"X-API-Key-1": "true"}, "path_parameters": {"bar": "null%2Cnull"}, "query": {"key": "true"}},
            {"headers": {"X-API-Key-1": "true"}, "path_parameters": {"bar": "AAA"}, "query": {"key": "true"}},
            {"headers": {"X-API-Key-1": "true"}, "path_parameters": {"bar": "null"}, "query": {"key": "true"}},
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
def test_underspecified_path_parameters(ctx, cli, app_runner, snapshot_cli, schema):
    # There should be no "Path parameter 'organization_id' is not defined"
    paths = {
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
    full_schema = ctx.openapi.build_schema(paths)
    app = ctx.openapi.make_permissive_flask_app(full_schema)
    base_url = app_runner.openapi_url(app, path="")
    schema_path = ctx.openapi.write_schema(paths)
    assert (
        cli.run(
            str(schema_path),
            f"--url={base_url}/api",
            "--phases=coverage",
        )
        == snapshot_cli
    )


def test_path_parameters_arent_missing(ctx, cli, snapshot_cli):
    # When `--mode=negative`, still generate path parameters if they can't be negated
    api = ctx.openapi.apps.success()
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
            f"--url={api.base_url}/api",
            "--checks=not_a_server_error",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


@pytest.mark.filterwarnings("error")
def test_path_parameters_without_schema(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
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
            f"--url={api.base_url}/api",
            "--checks=not_a_server_error",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2 m above gnd", "2%20m%20above%20gnd"),
        (".", "%2E"),
        ("..", "%2E%2E"),
        ("a+b", "a%2Bb"),
    ],
)
def test_quote_path_parameter_space(value, expected):
    # GH-4252: coverage-phase path values must percent-encode spaces, not form-encode them
    assert quote_path_parameter(value) == expected


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


def test_parameters_only_negative_value_reaches_the_operations_own_method(ctx):
    # The template takes the first negative value and is only ever sent under some other method,
    # so a parameter with exactly one of them would otherwise go untested under its own.
    schema = build_schema(
        ctx,
        [
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "minLength": 1, "maxLength": 255},
            },
        ],
        path="/foo/{id}",
    )
    assert_negative_coverage(
        schema,
        [{"path_parameters": {"id": Pattern("0{256}$")}}],
        path=("/foo/{id}", "post"),
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
                "headers": {"X-API-Key-1": "null,null"},
            },
            {
                "headers": {"X-API-Key-2": "null,null"},
            },
            {
                "headers": {"X-API-Key-1": "null,null", "X-API-Key-2": "000000"},
            },
            {
                "headers": {"X-API-Key-1": "000000", "X-API-Key-2": "null,null"},
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
            {"headers": {"X-API-Key-1": "00000", "x-schemathesis-unknown-property": "42"}},
            {"headers": {"X-API-Key-1": ""}},
            {"headers": {"X-API-Key-1": "{}"}},
            {"headers": {"X-API-Key-1": "null,null"}},
            {"headers": {"X-API-Key-1": "null"}},
            {"headers": {"X-API-Key-1": "true"}},
            {"headers": {"X-API-Key-1": "0.5"}},
            {"headers": {"X-API-Key-1": "0"}},
            {"headers": {"X-API-Key-2": "0"}},
            {"headers": {"X-API-Key-1": "0", "X-API-Key-2": ""}},
            {"headers": {"X-API-Key-1": "0", "X-API-Key-2": "{}"}},
            {"headers": {"X-API-Key-1": "0", "X-API-Key-2": "null,null"}},
            {"headers": {"X-API-Key-1": "0", "X-API-Key-2": "null"}},
            {"headers": {"X-API-Key-1": "0", "X-API-Key-2": "true"}},
            {"headers": {"X-API-Key-1": "0", "X-API-Key-2": "0.5"}},
            {"headers": {"X-API-Key-1": "", "X-API-Key-2": "0"}},
            {"headers": {"X-API-Key-1": "{}", "X-API-Key-2": "0"}},
            {"headers": {"X-API-Key-1": "null,null", "X-API-Key-2": "0"}},
            {"headers": {"X-API-Key-1": "null", "X-API-Key-2": "0"}},
            {"headers": {"X-API-Key-1": "true", "X-API-Key-2": "0"}},
            {"headers": {"X-API-Key-1": "0.5", "X-API-Key-2": "0"}},
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
        [
            {},
            {"headers": {"X-API-Key-1": "{}"}},
            {"headers": {"X-API-Key-1": "null,null"}},
            {"headers": {"X-API-Key-1": "null"}},
            {"headers": {"X-API-Key-1": "true"}},
            {"headers": {"X-API-Key-1": "0.5"}},
            {"headers": {"X-API-Key-1": "0"}},
        ],
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
                {"body": ["AAA"]},
                {"body": [None]},
                {"body": [0]},
                {"body": {}},
                {"body": "AAA"},
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
                {"body": [{}, False, False]},
                {"body": [[None, None], False, False]},
                {"body": ["AAA", False, False]},
                {"body": [None, False, False]},
                {"body": [0, False, False]},
                {"body": {}},
                {"body": "AAA"},
                {},
                {"body": False},
                {"body": 0},
            ],
        ),
        (
            {
                "type": "array",
                "items": {
                    # No type, so the pattern binds strings only - every other type is a valid element.
                    "pattern": "[\\p{Tibetan}]+",
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
                    "body": "AAA",
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
    assert_negative_coverage(build_schema(ctx, body=schema), expected)


def test_string_with_format(ctx):
    operation = load_schema(
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
    )["/foo/{foo_id}"]["post"]

    def test(case):
        uuid.UUID(case.path_parameters["foo_id"], version=4)

    run_positive_test(operation, test)


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
                        "A",
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
            {"query": {"foo": "bar.spam.00000000000000000000000000000000000000000000000000"}},
            {"query": {"foo": "bar.spam.000000000000000000000000000000000000000000000000000"}},
            {"query": {"foo": "bar.spam.0"}},
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
            [
                foo_id("null%2Cnull"),
                foo_id("AAA"),
                foo_id("null"),
                foo_id("true"),
            ],
        ),
        (
            {"type": "string", "format": "date-time"},
            [
                foo_id("0"),
                foo_id("null%2Cnull"),
                foo_id("null"),
                foo_id("true"),
                foo_id("0.5"),
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
                "schema": {"pattern": "'^[-._\\p{Tibetan}]+$'"},
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
                "http://127.0.0.1/foo?q=true",
                "http://127.0.0.1/foo?q=0.5",
            ],
        ],
        [
            {"type": "array", "items": {"type": "string"}},
            False,
            [
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo?q=AAA",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=true",
                "http://127.0.0.1/foo?q=0.5",
            ],
        ],
        [
            {"type": "array", "items": {"type": "string", "pattern": "^[0-9]{3,5}$"}},
            False,
            [
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null&q=null",
                "http://127.0.0.1/foo?q=0",
                "http://127.0.0.1/foo?q=AAA",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=true",
                "http://127.0.0.1/foo?q=0.5",
            ],
        ],
        [
            {"type": "array", "items": {"type": "string", "pattern": "^[0-9]{3,5}$"}},
            True,
            [
                "http://127.0.0.1/foo?q=0&q=0",
                "http://127.0.0.1/foo",
                "http://127.0.0.1/foo?q=",
                "http://127.0.0.1/foo?q=null&q=null",
                "http://127.0.0.1/foo?q=0",
                "http://127.0.0.1/foo?q=AAA",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=true",
                "http://127.0.0.1/foo?q=0.5",
            ],
        ],
    ],
)
def test_negative_query_parameter(ctx, schema, expected, required):
    schema = load_schema(
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


def test_negative_data_rejection(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
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
            f"--url={api.base_url}/api",
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
    operation = body_operation(
        ctx,
        {
            "properties": {"data": inner},
            "type": "object",
        },
        parameters=[
            {"in": "query", "name": "strict", "schema": {}},
        ],
        path="/items",
    )

    def test(case):
        # Body is `required`, hence should never be unset for positive tests
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


@pytest.mark.parametrize("required", [["name"], ["name", "description"]])
def test_request_body_with_references(ctx, required):
    operation = body_operation(
        ctx,
        {
            "properties": {"data": {"$ref": "#/components/schemas/Item"}},
            "required": ["data"],
            "type": "object",
        },
        path="/items",
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

    def test(case):
        # Body is `required`, hence should never be unset for positive tests
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


def test_request_body_without_validation_keywords(ctx):
    operation = body_operation(ctx, {"x-something": True}, path="/items")

    def test(case):
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


def test_unspecified_http_methods(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
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
    schema = ctx.openapi.load_schema(raw_schema)

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
                f"--url={api.base_url}/api",
                "--mode=negative",
                "--max-examples=10",
                "--continue-on-failure",
                hooks=module,
            )
            == snapshot_cli
        )


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
    schema = ctx.openapi.load_schema(raw_schema)

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


def test_avoid_testing_unexpected_methods_in_cli(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
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
            f"--url={api.base_url}/api",
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


def test_coverage_failure_shows_actual_method_in_header(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
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
            f"--url={api.base_url}/api",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


def test_missing_authorization(ctx, cli, snapshot_cli):
    # The reproduction code should not contain auth if it is explicitly specified
    api = ctx.openapi.apps.failure()
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
            f"--url={api.base_url}/api",
            "--header=Authorization: Bearer SECRET",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


def test_unnecessary_auth_warning(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.basic()
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
            f"--url={api.base_url}/api",
            "--header=Authorization: Basic dGVzdDp0ZXN0",
            "--max-examples=5",
        )
        == snapshot_cli
    )


def _unspecified_method_cases(operation):
    return scenario_cases(collect_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.UNSPECIFIED_HTTP_METHOD)


def test_nested_parameters(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "range",
                "in": "query",
                "content": {
                    "application/json": {
                        "schema": {"type": "null"},
                    },
                },
            }
        ],
        path="/test",
        method="get",
    )["/test"]["get"]

    assert {case.query["range"] for case in _unspecified_method_cases(operation)} == {"0"}


@pytest.mark.parametrize(
    ["operation", "components"],
    [
        (
            {
                "requestBody": make_request_body(
                    {"properties": {"p1": {"$ref": "#components/schemas/Key"}}}, required=None
                )
            },
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
            {"requestBody": make_request_body({"$ref": "#components/schemas/Key"}, required=None)},
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
    schema = ctx.openapi.load_schema({"/test": {"post": operation}}, components=components)
    for operation in schema.get_all_operations():
        if isinstance(operation, Ok):
            iter_cases(operation.ok(), *GenerationMode)
        else:
            assert "Unresolvable reference in the schema" in str(operation.err())


def test_urlencoded_array_body_is_serializable(ctx):
    # Form-urlencoded bodies declared as top-level arrays used to abort the operation when prepared.
    operation = body_operation(
        ctx,
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
        },
        media_type="application/x-www-form-urlencoded",
    )
    config = SanitizationConfig(enabled=False)
    count = 0
    for case in iter_cases(operation, *GenerationMode):
        prepare_request(case, headers=None, config=config)
        count += 1
    assert count > 0


def test_urlencoded_payloads_are_valid(ctx):
    operation = load_schema(
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
    )["/foo"]["post"]

    def test(case):
        if case.meta.phase != TestPhase.COVERAGE:
            return
        assert_requests_call(case)

    run_test(operation, test)


def test_malformed_content_type(ctx):
    operation = body_operation(ctx, {"type": "object"}, media_type="invalid")

    def test(case):
        if case.meta.phase != TestPhase.COVERAGE:
            return
        assert_requests_call(case)

    with pytest.raises(InvalidSchema):
        run_test(operation, test)


def test_no_missing_header_duplication(ctx):
    schema = load_schema(
        ctx,
        [
            {"name": "X-Key-1", "in": "header", "required": False, "schema": {"type": "string"}},
            {"name": "X-Key-2", "in": "header", "required": False, "schema": {"type": "string"}},
            {"name": "X-Key-3", "in": "header", "required": True, "schema": {"type": "string"}},
        ],
    )

    descriptions = []
    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        descriptions.append(case.meta.phase.data.description)

    run_test(operation, test)

    assert "Missing required property: X-Key-3" not in descriptions
    assert "Missing `X-Key-3` at header" in descriptions


def test_binary_format_should_not_generate_empty_string_as_invalid(ctx, cli, snapshot_cli):
    raw_schema = build_schema(
        ctx,
        body={
            "type": "string",
            "format": "binary",
        },
        media_type="application/octet-stream",
        parameters=[{"in": "path", "name": "filename", "required": True, "schema": {"type": "string"}}],
        path="/files/{filename}",
        method="put",
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/files/<path:filename>", methods=["PUT"])
    def upload_file(filename):
        data = request.get_data()
        return jsonify({"message": "File added successfully", "size": len(data)}), 201

    assert (
        cli.run_openapi_app(
            app,
            "-c",
            "negative_data_rejection",
            "--mode=negative",
            "--max-examples=50",
            "--phases=coverage",
        )
        == snapshot_cli
    )


def test_negative_type_violation_for_const_property(ctx):
    operation = body_operation(
        ctx,
        {
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
        },
        path="/test",
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

    cases = collect_cases(operation, GenerationMode.NEGATIVE)

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
    operation = body_operation(ctx, {"type": "object", "additionalProperties": {"type": "string"}})
    cases = collect_cases(operation, GenerationMode.POSITIVE)

    # Should generate objects with string values
    with_string_values = [
        c for c in cases if isinstance(c.body, dict) and any(isinstance(v, str) for v in c.body.values())
    ]
    assert len(with_string_values) > 0, (
        f"Should generate objects with string values. Got bodies: {[c.body for c in cases]}"
    )


def test_additional_properties_without_type_positive(ctx):
    # Azure swagger 2.0 schemas commonly omit `type: object` on tag maps; the implicit object
    # must still get a positive case satisfying `additionalProperties` so coverage flips `valid`.
    operation = body_operation(ctx, {"properties": {"tags": {"additionalProperties": {"type": "string"}}}})
    cases = collect_cases(operation, GenerationMode.POSITIVE)

    with_string_value = [
        c
        for c in cases
        if isinstance(c.body, dict)
        and isinstance(c.body.get("tags"), dict)
        and any(isinstance(v, str) for v in c.body["tags"].values())
    ]
    assert with_string_value, (
        f"Expected a positive case with a string-valued additional property under 'tags'. "
        f"Got bodies: {[c.body for c in cases]}"
    )


def test_items_without_type_positive(ctx):
    # Swagger 2.0 schemas commonly omit `type: array` on properties carrying only `items`
    # (clearblade.com et al.). Without an array-typed positive case, the items sub-schema
    # never gets a valid value and referenced definitions stay uncovered.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "change": {
                    "items": {
                        "type": "object",
                        "properties": {
                            "add": {"type": "string"},
                            "remove": {"type": "string"},
                        },
                    }
                }
            },
        },
    )
    cases = collect_cases(operation, GenerationMode.POSITIVE)

    with_valid_array = [
        c
        for c in cases
        if isinstance(c.body, dict)
        and isinstance(c.body.get("change"), list)
        and c.body["change"]
        and all(
            isinstance(item, dict)
            and (isinstance(item.get("add"), str) or "add" not in item)
            and (isinstance(item.get("remove"), str) or "remove" not in item)
            for item in c.body["change"]
        )
    ]
    assert with_valid_array, (
        f"Expected a positive case with 'change' as a non-empty array of valid items. "
        f"Got bodies: {[c.body for c in cases]}"
    )


def test_additional_properties_with_schema_negative(ctx):
    operation = body_operation(ctx, {"type": "object", "additionalProperties": {"type": "string"}})
    cases = collect_cases(operation, GenerationMode.NEGATIVE)

    # Should generate objects with non-string values (type violations)
    with_invalid_values = [
        c for c in cases if isinstance(c.body, dict) and any(not isinstance(v, str) for v in c.body.values())
    ]
    assert len(with_invalid_values) > 0, (
        f"Should generate objects with non-string values. Got bodies: {[c.body for c in cases]}"
    )


def test_negative_unexpected_property_avoids_pattern_properties(ctx):
    # The injected unexpected key must not match `patternProperties`, else the negative body stays valid.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "patternProperties": {"^x-": {"type": "integer"}},
            "additionalProperties": False,
            "properties": {"x-a": {"type": "integer"}},
            "required": ["x-a"],
        },
        positive=False,
        version="3.1.0",
    )


def test_negative_additional_property_value_avoids_pattern_properties(ctx):
    # A negative additionalProperties value must land on a key the patternProperties don't validate,
    # else it is checked against the pattern schema and may stay valid.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "patternProperties": {"^x-": {"type": "integer"}},
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        positive=False,
        version="3.1.0",
    )


def test_negative_type_drops_false_negatives_against_loose_ref_target(ctx):
    # Property's schema is `$ref` + sibling `type: object`. Draft 4 ignores siblings of `$ref`,
    # so the validator only enforces the bare ref target — which has no `type`. Type-mutations
    # against the silenced sibling pass the target vacuously and must not be emitted.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["thing"],
            "properties": {"thing": {"$ref": "#/definitions/Loose", "type": "object"}},
        },
        version="2.0",
        definitions={"Loose": {"properties": {"x": {"type": "string"}}, "required": ["x"]}},
    )
    assert_bodies(
        operation, GenerationMode.NEGATIVE, valid=False, validator_cls=operation.schema.adapter.jsonschema_validator_cls
    )


# Second component forces bundling — single-definition schemas get inlined and lose
# the `$ref` + sibling shape that triggers the bug.
BUNDLING_PADDING = {"Sku": {"type": "object", "properties": {"name": {"type": "string"}}}}


def test_negative_required_drops_false_negatives_at_body_root_with_ref_sibling(ctx):
    # Body root is `$ref` + sibling `required: [...]`. Draft 4 ignores siblings of `$ref`,
    # so the validator only enforces the bare ref target — which has no matching `required`.
    # Removing the listed required field passes the target vacuously and must not be emitted.
    operation = body_operation(
        ctx,
        {"$ref": "#/definitions/Wrapper", "required": ["location"]},
        version="2.0",
        definitions={
            "Wrapper": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "sku": {"$ref": "#/definitions/Sku"},
                },
            },
            **BUNDLING_PADDING,
        },
    )
    assert_bodies(
        operation, GenerationMode.NEGATIVE, valid=False, validator_cls=operation.schema.adapter.jsonschema_validator_cls
    )


def test_negative_ref_sibling_with_binary_format_does_not_crash_validator(ctx):
    # `$ref` + sibling triggers the unmerged-validator path; the merged target produces
    # values containing Binary, which jsonschema_rs cannot validate and raises ValueError.
    operation = body_operation(
        ctx,
        {
            "$ref": "#/components/schemas/Upload",
            "required": ["file"],
        },
        path="/upload",
        components={
            "schemas": {
                "Upload": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "sku": {"$ref": "#/components/schemas/Sku"},
                    },
                },
                **BUNDLING_PADDING,
            }
        },
    )

    assert iter_cases(operation, GenerationMode.NEGATIVE)


def test_positive_body_generated_for_object_with_metadata_and_unsatisfiable_optionals(ctx):
    # Object schema with metadata keyword (`title`) plus optional properties that are
    # unsatisfiable (`{"not": {}}` from readOnly). Empty `{}` is a valid positive body;
    # the generator must produce at least one rather than falling back on a negative body.
    operation = body_operation(
        ctx,
        {
            "title": "Resource",
            "type": "object",
            "properties": {
                "id": {"not": {}},
                "created_at": {"not": {}},
                "name": {"type": "string"},
            },
        },
        version="2.0",
    )

    positive_bodies = [
        case.body
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]
    assert positive_bodies, "Expected at least one positive body case"
    assert all(isinstance(body, dict) for body in positive_bodies), (
        f"Positive bodies must be objects per `type: object`; got: {positive_bodies}"
    )


def test_positive_body_generated_when_required_excludes_forbidden_properties(ctx):
    # A `readOnly` field listed in `required` must not block positive body generation.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "allOf": [{"type": "object"}],
            "properties": {
                "id": {"type": "string", "readOnly": True},
                "name": {"type": "string"},
            },
            "required": ["id", "name"],
        },
        version="2.0",
    )
    positive_bodies = [
        case.body
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]
    assert positive_bodies, "Expected at least one positive body case"
    assert all("id" not in body for body in positive_bodies), (
        f"Positive bodies must not contain forbidden `id`; got: {positive_bodies}"
    )


def test_positive_body_omits_property_forbidden_by_all_of_sibling(ctx):
    # A property carrying an `example` stays out of the body when another `allOf` branch marks it `readOnly`.
    operation = body_operation(
        ctx,
        {
            "allOf": [
                {"$ref": "#/components/schemas/Volume"},
                {"type": "object", "properties": {"linode_id": {"readOnly": True}}},
            ]
        },
        path="/volumes",
        method="put",
        components={
            "schemas": {
                "Volume": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "example": "my-volume"},
                        "linode_id": {"type": "integer", "nullable": True, "example": 12346},
                        "tags": {"$ref": "#/components/schemas/Tags"},
                    },
                },
                # Second component forces bundling so the `$ref` + sibling shape survives into generation.
                "Tags": {"type": "array", "items": {"type": "string"}},
            }
        },
    )
    positive_bodies = [
        case.body
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]
    assert positive_bodies, "Expected at least one positive body case"
    assert [body for body in positive_bodies if "linode_id" in body] == []


def test_parameter_positive_coverage_when_body_fallback_negative(ctx):
    # An unsatisfiable body must not suppress positive coverage of unrelated parameters.
    operation = body_operation(
        ctx,
        {
            "oneOf": [
                {"type": "object", "properties": {"channel": {"type": "string"}}},
                {"type": "object", "properties": {"channel": {"type": "string"}}},
            ]
        },
        body_required=None,
        parameters=[
            {
                "in": "query",
                "name": "format",
                "schema": {"type": "string", "enum": ["json", "jsonp", "msgpack", "html"]},
            }
        ],
        path="/push",
    )
    assert {
        case.query.get("format")
        for case in iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
        if case.query
        and (query_component := case.meta.components.get(ParameterLocation.QUERY)) is not None
        and query_component.mode == GenerationMode.POSITIVE
    } == {"json", "jsonp", "msgpack", "html"}


def test_parameter_mutation_cases_do_not_inherit_negative_body(ctx):
    # When positive body coverage yields nothing (the body schema combines `allOf` with
    # readOnly properties, so template inflation requires fields rewritten to `{"not": {}}`),
    # the engine previously fell back to a negative body as the template substrate.
    # Subsequent parameter-mutation cases (missing required header etc.) inherited that
    # negative body and emitted cases that mix two negatives. Verify no such case is emitted.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "allOf": [{"type": "object"}],
            "properties": {"id": {"readOnly": True, "type": "string"}},
        },
        parameters=[{"in": "header", "name": "X-Token", "required": True, "type": "string"}],
        version="2.0",
    )

    assert [
        (case.meta.phase.data.description, case.body)
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter_location != ParameterLocation.BODY
        and body_mode(case) == GenerationMode.NEGATIVE
    ] == []


def test_duplicate_items_case_leaves_the_declared_example_alone(ctx):
    # The duplicated-items value is built from the declared example, so rewriting its booleans into
    # their wire form must not reach back into the example every other case is built from.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "array",
                    "uniqueItems": True,
                    "minItems": 1,
                    "example": [{"enabled": True}],
                    "items": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                    },
                }
            },
        },
        parameters=[{"in": "header", "name": "X-Token", "required": True, "schema": {"type": "string"}}],
    )
    parameter_cases = [
        case
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter_location != ParameterLocation.BODY
    ]
    assert all(body_mode(case) == GenerationMode.POSITIVE for case in parameter_cases)
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, cases=parameter_cases)


def test_positive_number_near_boundary_respects_multiple_of(ctx):
    # IEEE-754 subtraction `maximum - multipleOf` drifts (e.g. `99999.99 - 0.01 = 99999.98000000001`).
    # The validator rejects the drifted value as not a multiple. Decimal-based arithmetic stays exact.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"amount": {"type": "number", "minimum": 0, "maximum": 99999.99, "multipleOf": 0.01}},
        },
        version="2.0",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_positive_number_boundary_respects_exclusive_bounds(ctx):
    # Boolean `exclusiveMinimum: true` + `exclusiveMaximum: true` combined with `minimum: 0`
    # / `maximum: 1` (legacy OpenAPI 3.0 form). The boundary generator's `+= 1` / `-= 1`
    # adjustments overshoot the other exclusive boundary; emitted values must validate.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "decayFactor": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "exclusiveMinimum": True,
                    "exclusiveMaximum": True,
                }
            },
        },
        version="2.0",
    )
    assert_bodies(
        operation, GenerationMode.POSITIVE, valid=True, validator_cls=operation.schema.adapter.jsonschema_validator_cls
    )


def test_additional_properties_anyof_positive(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
        },
    )
    cases = collect_cases(operation, GenerationMode.POSITIVE)

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
    exceeding = scenario_cases(cases, CoverageScenario.OBJECT_ABOVE_MAX_PROPERTIES)
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
    below = scenario_cases(cases, CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES)
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
    empty = scenario_cases(cases, CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES)
    assert len(empty) > 0, (
        f"Should generate OBJECT_BELOW_MIN_PROPERTIES for minProperties: 1. Got: {[c.body for c in cases]}"
    )
    assert any(c.body == {} for c in empty), (
        f"Should generate empty object for minProperties: 1. Got: {[c.body for c in empty]}"
    )


def test_min_properties_one_with_additional_properties(ctx):
    cases = collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
            "minProperties": 1,
            "maxProperties": 2,
        },
    )
    assert any(c.body == {} for c in scenario_cases(cases, CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES)), (
        f"Should generate empty object for minProperties: 1 alongside additionalProperties. Got: {[c.body for c in cases]}"
    )


def test_anyof_with_outer_properties_yields_branch_constrained_bodies(ctx):
    # Outer property `status: string` is tightened by each anyOf branch via enum;
    # positive bodies must satisfy at least one branch's enum.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"status": {"type": "string"}},
            "anyOf": [
                {"properties": {"status": {"enum": ["succeeded"]}}},
                {"properties": {"status": {"enum": ["failed", "rejected"]}}},
            ],
        },
        path="/x",
    )
    cases = generate_cases(operation, GenerationMode.POSITIVE)
    bad = [
        c.body
        for c in cases
        if isinstance(c.body, dict)
        and "status" in c.body
        and c.body["status"] not in ("succeeded", "failed", "rejected")
    ]
    assert not bad, f"Positive body must satisfy at least one anyOf branch's enum. Got: {bad}"


def test_oneof_no_required_disambiguator_does_not_yield_ambiguous_empty(ctx):
    # Both oneOf branches accept `{}` (no required, only optional properties).
    # `{}` matches both, violating oneOf's "exactly one" — must not be yielded as a positive case.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "oneOf": [
                {"properties": {"a": {"type": "integer"}}},
                {"properties": {"b": {"type": "integer"}}},
            ],
        },
        path="/x",
    )
    cases = generate_cases(operation, GenerationMode.POSITIVE)
    assert not any(c.body == {} for c in cases), (
        f"Empty `{{}}` matches both oneOf branches and must not be yielded. Got: {[c.body for c in cases]}"
    )


def test_anyof_discriminator_branch_required_propagated(ctx):
    # anyOf branches discriminated by a `type` enum. The branch with type=A also requires
    # `priority`. A positive body claiming type=A must include priority.
    operation = body_operation(
        ctx,
        {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "type": {"enum": ["A"]},
                        "value": {"type": "string"},
                        "priority": {"type": "integer"},
                    },
                    "required": ["type", "value", "priority"],
                },
                {
                    "type": "object",
                    "properties": {
                        "type": {"enum": ["B"]},
                        "value": {"type": "string"},
                    },
                    "required": ["type", "value"],
                },
            ],
        },
        path="/x",
    )
    cases = generate_cases(operation, GenerationMode.POSITIVE)
    bad = [c.body for c in cases if isinstance(c.body, dict) and c.body.get("type") == "A" and "priority" not in c.body]
    assert not bad, f"Positive body for branch type=A must include branch-required `priority`. Got: {bad}"


def test_request_body_example_invalid_against_schema_not_yielded(ctx):
    # Boolean `exclusiveMinimum` (Draft 4) defeats Draft-2020-12 auto-detection; the example
    # missing `riskFreeRate` must still be filtered out as a positive case.
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "portfolios": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "values": {
                                            "type": "array",
                                            "items": {
                                                "type": "number",
                                                "minimum": 0,
                                                "exclusiveMinimum": True,
                                            },
                                            "minItems": 2,
                                        },
                                    },
                                    "required": ["values"],
                                },
                                "minItems": 1,
                            },
                            "riskFreeRate": {"type": "number"},
                        },
                        "required": ["portfolios", "riskFreeRate"],
                    },
                    "examples": {
                        "missing-required": {
                            "value": {"portfolios": [{"values": [100, 95]}]},
                        },
                    },
                }
            },
        },
        path="/x",
    )["/x"]["POST"]
    cases = generate_cases(operation, GenerationMode.POSITIVE)
    bad = [c.body for c in cases if isinstance(c.body, dict) and "riskFreeRate" not in c.body]
    assert not bad, f"Spec example invalid against schema must not be yielded. Got: {bad}"


def test_required_outside_allof_propagated_into_canonicalised_branches(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "schedule": {"$ref": "#/components/schemas/Wrapper"},
            },
        },
        path="/x",
        components={
            "schemas": {
                "Interval": {"type": "string", "enum": ["WEEKLY", "MONTHLY"]},
                "Base": {
                    "type": "object",
                    "additionalProperties": True,
                    "nullable": True,
                    "properties": {
                        "adjusted_start_date": {"type": "string", "format": "date", "nullable": True},
                        "end_date": {"type": "string", "format": "date", "nullable": True},
                        "start_date": {"type": "string", "format": "date"},
                        "interval": {"$ref": "#/components/schemas/Interval"},
                        "interval_execution_day": {"type": "integer"},
                    },
                },
                "Wrapper": {
                    "additionalProperties": True,
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {"type": "object"},
                    ],
                    "required": ["start_date", "interval", "interval_execution_day"],
                },
            }
        },
    )
    cases = collect_cases(operation, GenerationMode.POSITIVE)

    required = ("start_date", "interval", "interval_execution_day")
    bad = []
    for c in cases:
        if not isinstance(c.body, dict):
            continue
        sched = c.body.get("schedule")
        if isinstance(sched, dict) and not all(k in sched for k in required):
            bad.append(sched)
    assert not bad, f"Generated nested object missing outer-required properties. Got: {bad}"


def test_positive_body_under_allof_with_optional_outer_property_only(ctx):
    # Base's `additionalProperties: false` forbids the outer's only optional property in positive cases.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": False,
            "allOf": [{"$ref": "#/components/schemas/Base"}],
            "properties": {"properties": {"properties": {"x": {"type": "string"}}}},
        },
        path="/x",
        components={
            "schemas": {
                "Base": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"etag": {"type": "string"}},
                }
            }
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


def test_positive_body_under_unsatisfiable_allof_chain(ctx):
    # Outer's `required` key is absent from a base with `additionalProperties: false`,
    # so the strict canonical schema is unsatisfiable.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"payload": {"$ref": "#/components/schemas/Wrapper"}},
            "required": ["payload"],
        },
        path="/x",
        components={
            "schemas": {
                "Base": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"baseField": {"type": "string"}},
                },
                "Wrapper": {
                    "type": "object",
                    "additionalProperties": False,
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "properties": {
                        "first": {"type": "string"},
                        "second": {"type": "string"},
                    },
                    "required": ["first", "second"],
                },
            }
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


def test_positive_body_with_sibling_oneof_required_via_ref(ctx):
    # Sibling `oneOf: [{required: [a]}, {required: [b]}]` makes a and b mutually exclusive;
    # the combinator filter needs the root bundle attached to resolve sub-refs and apply it.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"inner": {"$ref": "#/components/schemas/Inner"}},
        },
        path="/x",
        version="3.1.0",
        components={
            "schemas": {
                "Inner": {
                    "type": "object",
                    "properties": {
                        "a": {"$ref": "#/components/schemas/Leaf"},
                        "b": {"$ref": "#/components/schemas/Leaf"},
                    },
                    "oneOf": [{"required": ["a"]}, {"required": ["b"]}],
                },
                "Leaf": {"type": "array", "items": {"type": "string"}},
            }
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


def test_ref_with_type_sibling_dropped_in_openapi_3_0(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "Field": {
                    "$ref": "#/components/schemas/Inner",
                    "type": "string",
                },
            },
        },
        path="/x",
        components={
            "schemas": {
                "Inner": {
                    "type": "object",
                    "properties": {"foo": {"type": "string"}},
                    "required": ["foo"],
                },
            }
        },
    )
    cases = collect_cases(operation, GenerationMode.POSITIVE)

    field_strings = [c.body for c in cases if isinstance(c.body, dict) and isinstance(c.body.get("Field"), str)]
    assert not field_strings, f"Field generated as string despite $ref to object. Got: {field_strings}"


def test_additional_property_respects_max_properties(ctx):
    cases = collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 1,
            "additionalProperties": {"type": "integer"},
        },
        positive=True,
    )
    exceeding = [
        c
        for c in scenario_cases(cases, CoverageScenario.OBJECT_ADDITIONAL_PROPERTY)
        if isinstance(c.body, dict) and len(c.body) > 1
    ]
    assert not exceeding, (
        f"OBJECT_ADDITIONAL_PROPERTY positive case must respect maxProperties. Got: {[c.body for c in exceeding]}"
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
    below = scenario_cases(cases, CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES)
    assert len(below) == 0, (
        f"Should NOT generate OBJECT_BELOW_MIN_PROPERTIES when required > minProperties. Got: {below}"
    )


def test_missing_content_type_header(ctx):
    # Regression: "missing Content-Type header" test case should not include Content-Type in request
    operation = body_operation(
        ctx,
        {"type": "object"},
        parameters=[
            {"in": "header", "name": "Content-Type", "schema": {"type": "string"}, "required": True},
        ],
    )

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


def test_path_template_with_dot_prefixed_placeholder(ctx):
    # RFC 6570 label expansion (`{.format}`) appears in real schemas; coverage used to abort the operation.
    operation = load_schema(
        ctx,
        path="/projects/{id}{.format}",
        method="get",
        parameters=[
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": ".format", "in": "path", "required": True, "schema": {"type": "string", "enum": ["json"]}},
        ],
    )["/projects/{id}{.format}"]["get"]
    config = SanitizationConfig(enabled=False)
    paths = set()
    for case in iter_cases(operation, *GenerationMode):
        prepared = prepare_request(case, headers=None, config=config)
        paths.add(prepared.url)
    assert paths


def test_path_parameter_with_slash_in_custom_format(ctx):
    # See GH-3527
    schemathesis.openapi.format("ipv4-network", st.sampled_from(["0.0.0.0/0"]))
    operation = load_schema(
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
    )["/blocks/{block}"]["get"]

    path_values = [case.path_parameters.get("block") for case in collect_cases(operation, GenerationMode.POSITIVE)]

    assert path_values, "No coverage cases generated"
    assert all(v == "0.0.0.0%2F0" for v in path_values), f"Unexpected values: {path_values}"


def test_xml_string_field_no_type_mutations(ctx):
    # For {"type": "string"} XML fields, type mutations produce the same wire bytes as valid strings.
    # None -> "", False -> "False", 0 -> "0" all become valid string content in XML elements.
    cases = collect_cases(
        body_operation(
            ctx,
            {"type": "object", "properties": {"x-prop": {"type": "string"}}, "required": ["x-prop"]},
            media_type="application/xml",
        ),
        GenerationMode.NEGATIVE,
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
    cases = collect_cases(
        body_operation(
            ctx,
            {"type": "object", "properties": {"x-prop": {"type": "string", "minLength": 5}}, "required": ["x-prop"]},
            media_type="application/xml",
        ),
        GenerationMode.NEGATIVE,
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
    cases = collect_cases(
        body_operation(
            ctx, {"type": "object", "properties": {"x-prop": {"type": "string"}}}, media_type="application/xml"
        ),
        GenerationMode.NEGATIVE,
    )
    ambiguous = [c for c in cases if c.body is None or c.body == ""]
    assert ambiguous == [], (
        f"Null/empty-string body mutations should not be generated for XML object bodies, got: {ambiguous}"
    )


def test_xml_none_property_mutation_filtered_when_schema_accepts_empty_string(ctx):
    # For XML string fields, _escape_xml(None) = "" (not "None").
    # Schema {"type": "string", "maxLength": 0} accepts only "" — None should NOT be generated
    # because it produces the same valid wire content.
    cases = collect_cases(
        body_operation(
            ctx,
            {"type": "object", "properties": {"x-prop": {"type": "string", "maxLength": 0}}, "required": ["x-prop"]},
            media_type="application/xml",
        ),
        GenerationMode.NEGATIVE,
    )
    null_property_mutations = [
        c for c in cases if isinstance(c.body, dict) and "x-prop" in c.body and c.body["x-prop"] is None
    ]
    assert null_property_mutations == [], (
        f"None mutation for XML string field with maxLength:0 should be filtered, got: {null_property_mutations}"
    )


def test_xml_string_leaf_has_non_empty_positive_case(ctx):
    # Empty XML elements bypass server-side string-keyword validators on common parsers.
    cases = collect_cases(
        body_operation(
            ctx,
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            media_type="application/xml",
        ),
        GenerationMode.POSITIVE,
    )
    populated = [
        c.body for c in cases if isinstance(c.body, dict) and isinstance(c.body.get("name"), str) and c.body["name"]
    ]
    assert populated, f"Expected at least one positive case with a non-empty 'name'; got: {[c.body for c in cases]}"


def test_xml_optional_ref_object_property_populated_in_positive_cases(ctx):
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Wrapper"},
        media_type="application/xml",
        components={
            "schemas": {
                "Wrapper": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "child": {"$ref": "#/components/schemas/Child"},
                    },
                    "required": ["id"],
                },
                "Child": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            }
        },
    )
    cases = collect_cases(operation, GenerationMode.POSITIVE)
    with_child = [
        c.body
        for c in cases
        if isinstance(c.body, dict) and isinstance(c.body.get("child"), dict) and "value" in c.body["child"]
    ]
    assert with_child, (
        f"Expected at least one positive case populating optional 'child'; got: {[c.body for c in cases]}"
    )


def test_query_method_appears_in_unspecified_methods(ctx):
    operation = load_schema(ctx, path="/search", version="3.2.0")["/search"]["post"]

    assert "QUERY" in {case.method for case in _unspecified_method_cases(operation)}


def test_query_method_excluded_from_unexpected_when_defined(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/search": {
                "query": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
        version="3.2.0",
    )
    operation = schema["/search"]["post"]

    assert "QUERY" not in {case.method for case in _unspecified_method_cases(operation)}


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
    operation = load_schema(
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
    )["/foo"]["post"]
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


def test_all_of_branch_judging_outer_properties_as_additional(ctx):
    # The branch sees the outer schema's own properties as additional, so folding the two property
    # sets together would admit values the branch rejects.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Outer"},
        path="/x",
        components={
            "schemas": {
                "Base": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "properties": {"a": {"type": "string"}},
                },
                "Outer": {
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "type": "object",
                    "properties": {"b": {"type": "boolean"}},
                },
            }
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_all_of_branch_that_stays_a_reference(ctx):
    # A branch left as a bare reference cannot carry its siblings' constraints - `$ref` wins over them.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"data": {"$ref": "#/components/schemas/Outer"}},
        },
        path="/x",
        components={
            "schemas": {
                "Inner": {"allOf": [{"type": "object"}]},
                "Middle": {"allOf": [{"$ref": "#/components/schemas/Inner"}]},
                "Outer": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Middle"},
                        {"properties": {"workspace": {"type": "string"}}, "required": ["workspace"]},
                    ]
                },
            }
        },
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


@pytest.mark.parametrize(
    "branches",
    [
        [{"$ref": "#/components/schemas/Node"}],
        [{"$ref": "#/components/schemas/Node"}, {"type": "object"}],
    ],
    ids=["sole-branch", "beside-a-sibling"],
)
def test_reference_cycle_through_an_all_of_branch(ctx, branches):
    # Branches are resolved before the value is built, so the pointer never reaches the cycle counter.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Node"},
        path="/x",
        components={"schemas": {"Node": {"type": "object", "properties": {"child": {"allOf": branches}}}}},
    )
    assert iter_cases(operation, GenerationMode.NEGATIVE)


@pytest.mark.parametrize("combinator", ["oneOf", "anyOf"], ids=["one-of", "any-of"])
def test_reference_cycle_through_a_combinator_branch(ctx, combinator):
    # A branch resolved before the walk carries no `$ref` for the cycle guard to count.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Block"},
        path="/x",
        components={
            "schemas": {
                "Block": {
                    "type": "object",
                    "properties": {
                        "calls": {
                            "type": "array",
                            "items": {combinator: [{"$ref": "#/components/schemas/Block"}]},
                        }
                    },
                }
            }
        },
    )
    assert iter_cases(operation, GenerationMode.POSITIVE)


@pytest.mark.parametrize(
    "modes",
    [[GenerationMode.POSITIVE], [GenerationMode.POSITIVE, GenerationMode.NEGATIVE]],
    ids=["positive", "mixed"],
)
def test_unsatisfiable_required_param_emits_no_positive_case(ctx, modes):
    # An unsatisfiable required parameter leaves no valid positive request, even when mixed mode seeds the
    # template with a negative value.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "f",
                "in": "query",
                "required": True,
                "schema": {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
            }
        ],
        path="/route",
        method="get",
        version="3.1.0",
    )["/route"]["get"]
    cases = iter_cases(operation, *modes)
    positive = [case.query for case in cases if case.meta.generation.mode == GenerationMode.POSITIVE]
    assert positive == [], positive


def test_unsatisfiable_required_path_param_emits_no_positive_case(ctx):
    # A required path parameter falls back to a negative sample when nothing is representable; the positive
    # default case must still be suppressed rather than shipping that sample as positive.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "f",
                "in": "path",
                "required": True,
                "schema": {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
            }
        ],
        path="/items/{f}",
        method="get",
        version="3.1.0",
    )["/items/{f}"]["get"]
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    assert cases == [], [case.path_parameters for case in cases]


def test_unsatisfiable_required_param_suppresses_positive_from_other_params(ctx):
    # A second, satisfiable parameter must not produce any positive case while a sibling required
    # parameter is unsatisfiable: the whole operation has no valid positive request.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "f",
                "in": "query",
                "required": True,
                "schema": {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
            },
            {
                "name": "h",
                "in": "header",
                "required": False,
                "schema": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        ],
        path="/route",
        method="get",
        version="3.1.0",
    )["/route"]["get"]
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    positive = [case for case in cases if case.meta.generation.mode == GenerationMode.POSITIVE]
    assert positive == [], [(case.query, case.headers) for case in positive]


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
    operation = body_operation(
        ctx,
        body_schema,
        parameters=[
            {
                "name": "X-Required-Header",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        path="/test",
    )
    validator = operation.schema.adapter.jsonschema_validator_cls(body_schema, validate_formats=False)

    missing_header_cases = [
        case
        for case in scenario_cases(iter_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.MISSING_PARAMETER)
        if case.meta.phase.data.parameter == "X-Required-Header"
    ]

    assert missing_header_cases, "Expected at least one MISSING_PARAMETER case for X-Required-Header"
    # Template body must be valid so the server reaches header validation, not body rejection.
    assert all(validator.is_valid(case.body) for case in missing_header_cases), (
        f"Missing-header cases must have a valid body, got: {[case.body for case in missing_header_cases]}"
    )


def test_missing_required_header_case_respects_before_call_hook_restoring_header(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "X-Required-Header",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        path="/items",
        method="put",
    )["/items"]["put"]

    missing_header_case = next(
        case
        for case in scenario_cases(iter_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.MISSING_PARAMETER)
        if case.meta.phase.data.parameter == "X-Required-Header"
    )

    assert missing_header_case.meta.generation.mode == GenerationMode.NEGATIVE

    missing_header_case.headers["X-Required-Header"] = "restored"

    assert missing_header_case.meta.generation.mode == GenerationMode.POSITIVE

    kwargs = missing_header_case.as_transport_kwargs(base_url="http://127.0.0.1")
    assert kwargs["headers"].get("X-Required-Header") == "restored"


def test_filter_case_hook_applied_in_coverage_phase(ctx):
    loaded = load_schema(
        ctx,
        parameters=[{"name": "key", "in": "query", "schema": {"type": "integer"}}],
        method="get",
    )
    operation = loaded["/foo"]["get"]

    assert generate_cases(operation, GenerationMode.POSITIVE), "Expected coverage cases before filtering"

    @loaded.hook
    def filter_case(context, case):
        return False  # reject everything

    assert generate_cases(operation, GenerationMode.POSITIVE) == [], (
        "filter_case hook should suppress all coverage cases"
    )


def test_map_case_hook_applied_in_coverage_phase(ctx):
    loaded = load_schema(
        ctx,
        parameters=[{"name": "key", "in": "query", "schema": {"type": "integer"}}],
        method="get",
    )

    @loaded.hook
    def map_case(context, case):
        if case.query is not None:
            case.query["injected"] = "yes"
        return case

    operation = loaded["/foo"]["get"]
    cases = generate_cases(operation, GenerationMode.POSITIVE)

    assert cases, "Expected at least one coverage case"
    assert all(c.query is None or c.query.get("injected") == "yes" for c in cases), (
        "map_case hook should have injected 'injected' into every query"
    )


def test_content_json_query_params_single_encoding_in_coverage(ctx):
    # See GH-3701
    operation = body_operation(
        ctx,
        {"type": "array", "items": {"type": "string"}},
        parameters=[
            {
                "name": "filters",
                "in": "query",
                "required": True,
                "content": {"application/json": {"schema": {"type": "array", "example": []}}},
            },
        ],
    )

    cases = generate_cases(operation, GenerationMode.POSITIVE)

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


# YAML parses bare `on:` as boolean True, so schemas loaded from YAML can have bool keys in `properties`.
BOOLEAN_KEY_BODY_SCHEMA = {
    "type": "object",
    "properties": {
        True: {"type": "string"},
        "name": {"type": "string"},
    },
}


def test_coverage_body_with_boolean_property_key(ctx):
    assert iter_cases(body_operation(ctx, BOOLEAN_KEY_BODY_SCHEMA, path="/hooks"), GenerationMode.POSITIVE)


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
    operation = body_operation(ctx, body_schema, path="/zipcode")

    optimized_schema = optimized_body_schema(operation)
    assert "maxLength" in optimized_schema, f"maxLength must be preserved in optimized_schema; got: {optimized_schema}"

    max_length_cases = [
        case
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
        if isinstance(case.body, str) and len(case.body) > 10
    ]
    assert max_length_cases, "Expected at least one NEGATIVE case with a body string longer than maxLength=10"
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, cases=max_length_cases)


def test_coverage_positive_pattern_skipped_for_non_string_type(ctx):
    # When a schema has 'pattern' alongside a non-string 'type', the coverage
    # phase must not generate string values as POSITIVE cases — they violate 'type'
    # and are schema-invalid, causing false positive_data_acceptance failures.
    operation = body_operation(ctx, {"type": "number", "pattern": "[0-9]{4}"}, path="/pin")
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_coverage_positive_allof_ref_property_merge(ctx):
    # Multi-level allOf chain (Child -> Intermediate -> Base) where Base defines 'location'.
    # canonicalish leaves an unresolved $ref inside the merged schema; cover_schema_iter must
    # deep-merge 'properties' from the resolved ref, not overwrite, so 'location' stays present.
    operation = body_operation(
        ctx,
        {"$ref": "#/definitions/Child"},
        parameters=[{"name": "name", "in": "path", "required": True, "type": "string"}],
        path="/resources/{name}",
        method="put",
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_coverage_body_with_boolean_property_key_negative(ctx):
    operation = body_operation(
        ctx,
        BOOLEAN_KEY_BODY_SCHEMA,
        parameters=[
            {
                "name": "X-Hook-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        path="/hooks",
    )

    assert iter_cases(operation, GenerationMode.NEGATIVE)


def test_coverage_form_urlencoded_binary_format_negative(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["file", "name"],
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "name": {"type": "string"},
            },
        },
        media_type="application/x-www-form-urlencoded",
        path="/upload",
    )

    cases = generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        assert case.meta.phase.name == TestPhase.COVERAGE


def test_coverage_negative_empty_dict_additional_properties_not_treated_as_false(ctx):
    # `additionalProperties: {}` is equivalent to `true` — any extra property is valid.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "params": {
                    "type": "object",
                    "additionalProperties": {},
                },
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
        path="/search",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=generate_cases, validate_formats=False)


def test_coverage_negative_pattern_with_control_chars_uses_schema_validator(ctx):
    operation = body_operation(
        ctx,
        {
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
        },
        path="/items",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=generate_cases, validate_formats=False)


def test_coverage_positive_body_uuid_format_with_uppercase_pattern(ctx):
    # A property schema with format:uuid AND a pattern that restricts to uppercase hex
    # must generate a POSITIVE value that is valid for BOTH constraints - i.e. an
    # uppercase UUID with hyphens.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "templateId": {
                    "type": "string",
                    "format": "uuid",
                    "pattern": "^[0-9A-F]{8}[-]?[0-9A-F]{4}[-]?[0-9A-F]{4}[-]?[0-9A-F]{4}[-]?[0-9A-F]{12}$",
                }
            },
        },
        path="/docs",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=generate_cases)


def test_coverage_positive_body_skips_properties_with_no_valid_enum_values(ctx):
    # A property schema like {enum: ["MALE", "FEMALE"], maxLength: 1} has contradictory
    # constraints — all enum values violate maxLength. The coverage phase must not pick
    # an invalid enum value as the positive body template, causing POSITIVE body failures.
    operation = body_operation(
        ctx,
        {
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
        },
        path="/users",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=generate_cases, validate_formats=False)


def test_coverage_positive_object_type_with_items(ctx):
    # Schema property with type:"object" and "items" (a schema inconsistency) must not
    # cause generate_from_schema to produce a list — the items/type fast path must only
    # trigger for type:"array", not type:"object".
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["value"],
            "properties": {
                "ids": {
                    "type": "object",
                    "items": {"type": "string"},
                },
                "value": {"type": "string"},
            },
        },
        path="/register",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_object_example_with_readonly_key_ships_without_it(ctx):
    # A curated body `example` naming a server-set field must still ship once, minus that field.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/File"},
        path="/r",
        components={
            "schemas": {
                "File": {
                    "type": "object",
                    "example": {
                        "content": "Zm9v",
                        "content_path": "/v1/files/abc/content",
                        "id": "abc",
                        "name": "foo.txt",
                        "size": 35,
                    },
                    "properties": {
                        "content": {"type": "string"},
                        "content_path": {"type": "string", "readOnly": True},
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "size": {"type": "integer"},
                    },
                },
            },
        },
    )
    assert {"content": "Zm9v", "id": "abc", "name": "foo.txt", "size": 35} in [
        case.body for case in iter_cases(operation, GenerationMode.POSITIVE)
    ]


def test_example_with_nested_ref_violation_is_not_used(ctx):
    # An `example` whose nested values violate an enum reachable via `$ref` must not
    # be emitted as a positive case. Without bundle-aware validation the ref cannot
    # resolve, the validator silently accepts the example, and an invalid body ships.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Wrapper"},
        path="/r",
        components={
            "schemas": {
                "Wrapper": {
                    "type": "object",
                    "required": ["item"],
                    "properties": {"item": {"$ref": "#/components/schemas/Item"}},
                },
                "Item": {
                    "type": "object",
                    "required": ["choices"],
                    "example": {"choices": ["bad"]},
                    "properties": {
                        "choices": {"type": "array", "items": {"$ref": "#/components/schemas/Choice"}},
                    },
                },
                "Choice": {"type": "string", "enum": ["allowed"]},
            },
        },
    )
    resolved_body = {
        "type": "object",
        "required": ["item"],
        "properties": {
            "item": {
                "type": "object",
                "required": ["choices"],
                "properties": {
                    "choices": {"type": "array", "items": {"type": "string", "enum": ["allowed"]}},
                },
            },
        },
    }
    validator = jsonschema_rs.validator_for(resolved_body)
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    assert cases, "expected at least one positive coverage case"
    for case in cases:
        assert validator.is_valid(case.body), f"Invalid positive body emitted: {case.body!r}"


def test_content_example_invalid_under_draft4_only_schema_is_not_used(ctx):
    # Schemas mixing draft-specific keywords with content-level examples must not ship examples
    # whose values violate item-schemas (e.g. `null` in a `number` array) as positive coverage bodies.
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "examples": {
                        "bad": {"value": {"w": [0.5, None]}},
                        "good": {"value": {"w": [0.5, 0.5]}},
                    },
                    "schema": {
                        "type": "object",
                        "properties": {
                            "w": {
                                "type": "array",
                                "minItems": 2,
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "k": {
                                "type": "array",
                                "minItems": 2,
                                "items": {"type": "number", "minimum": 0, "exclusiveMinimum": True},
                            },
                        },
                    },
                }
            },
        },
        path="/r",
    )["/r"]["POST"]
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    assert cases, "expected at least one positive coverage case"
    for case in cases:
        body = case.body
        if isinstance(body, dict) and isinstance(body.get("w"), list):
            assert None not in body["w"], f"Invalid positive body emitted: {body!r}"


def test_oneof_ref_branches_with_discriminator_each_get_distinct_positive_coverage(ctx):
    # A nested discriminator `oneOf` under an outer `oneOf`-discriminated body must
    # yield at least one value uniquely satisfying each inner branch.
    raw = build_schema(
        ctx,
        body={"$ref": "#/components/schemas/Rule"},
        path="/r",
        components={
            "schemas": {
                "Rule": {
                    "discriminator": {
                        "propertyName": "ruleType",
                        "mapping": {
                            "http": "#/components/schemas/HttpRule",
                            "kinesis": "#/components/schemas/KinesisRule",
                        },
                    },
                    "oneOf": [
                        {"$ref": "#/components/schemas/HttpRule"},
                        {"$ref": "#/components/schemas/KinesisRule"},
                    ],
                },
                "HttpRule": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ruleType", "url"],
                    "properties": {
                        "ruleType": {"type": "string", "enum": ["http"]},
                        "url": {"type": "string"},
                    },
                },
                "KinesisRule": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ruleType", "target"],
                    "properties": {
                        "ruleType": {"type": "string", "enum": ["kinesis"]},
                        "target": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["auth"],
                            "properties": {
                                "auth": {
                                    "discriminator": {
                                        "propertyName": "mode",
                                        "mapping": {
                                            "credentials": "#/components/schemas/Credentials",
                                            "assumeRole": "#/components/schemas/AssumeRole",
                                        },
                                    },
                                    "oneOf": [
                                        {"$ref": "#/components/schemas/Credentials"},
                                        {"$ref": "#/components/schemas/AssumeRole"},
                                    ],
                                },
                            },
                        },
                    },
                },
                "Credentials": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["accessKey", "secretKey"],
                    "properties": {
                        "mode": {"type": "string", "enum": ["credentials"]},
                        "accessKey": {"type": "string"},
                        "secretKey": {"type": "string"},
                    },
                },
                "AssumeRole": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["roleArn"],
                    "properties": {
                        "mode": {"type": "string", "enum": ["assumeRole"]},
                        "roleArn": {"type": "string"},
                    },
                },
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(raw)
    operation = loaded["/r"]["POST"]
    creds_validator = jsonschema_rs.validator_for(raw["components"]["schemas"]["Credentials"])
    assume_validator = jsonschema_rs.validator_for(raw["components"]["schemas"]["AssumeRole"])
    creds_only = 0
    assume_only = 0
    for case in iter_cases(operation, GenerationMode.POSITIVE):
        body = case.body
        if not isinstance(body, dict) or not isinstance(body.get("target"), dict):
            continue
        auth = body["target"].get("auth")
        if not isinstance(auth, dict):
            continue
        ok_c = creds_validator.is_valid(auth)
        ok_a = assume_validator.is_valid(auth)
        if ok_c and not ok_a:
            creds_only += 1
        elif ok_a and not ok_c:
            assume_only += 1
    assert creds_only > 0 and assume_only > 0, f"creds_only={creds_only}, assume_only={assume_only}"


def test_coverage_negative_string_length_with_enum(ctx):
    operation = body_operation(
        ctx,
        {
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
        },
        path="/submit",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


def test_negative_enum_emits_entries_with_type_mismatch_for_keyword_coverage(ctx):
    # Positive path skips every entry as `type`-invalid, so only negatives can exercise `enum` here.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "chunk_size": {"enum": [2, 4, 6, 8, 10], "type": "string"},
            },
        },
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)
    emitted = {
        c.body["chunk_size"]
        for c in cases
        if isinstance(c.body, dict) and "chunk_size" in c.body and isinstance(c.body["chunk_size"], int)
    }
    assert {2, 4, 6, 8, 10}.issubset(emitted), f"Expected each enum entry as a negative; got: {emitted}"


@pytest.mark.parametrize(
    "property_schema",
    [
        {"type": "integer", "enum": [1, 2]},
        {"type": ["integer", "null"], "enum": [None, 301, 302, 307, 308]},
        {"type": "number", "enum": [1, 2, 3.5]},
    ],
    ids=["integer", "integer-or-null", "number-with-int-entries"],
)
def test_negative_enum_does_not_flag_integer_entries_matching_declared_type(ctx, property_schema):
    # Integer enum entries are valid under `type: integer` (and `type: number`); the
    # "Enum value with type mismatching" fallback must skip them, not emit them as negatives.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"value": property_schema},
        },
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


@pytest.mark.parametrize(
    ("body_schema", "expected"),
    [
        ({"type": "array", "items": {"type": "string", "enum": []}}, [[]]),
        ({"type": "array", "minItems": 1, "items": {"type": "string", "enum": []}}, []),
        ({"type": "array", "minItems": 1, "items": {"type": "string", "enum": [1, 2]}}, []),
    ],
    ids=["empty-enum", "empty-enum-with-min-items", "entries-violating-item-type"],
)
def test_positive_array_items_enum_without_usable_entries(ctx, body_schema, expected):
    # An empty array is the only conforming value when no entry is usable; requiring one item leaves nothing.
    assert [case.body for case in iter_cases(body_operation(ctx, body_schema), GenerationMode.POSITIVE)] == expected


def test_negative_const_emits_value_with_type_mismatch_for_keyword_coverage(ctx):
    # Positive path skips the const value as `type`-invalid, so only the negative can exercise `const` here.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "chunk_size": {"const": 42, "type": "string"},
            },
        },
        version="3.1.0",
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)
    emitted = {
        c.body["chunk_size"]
        for c in cases
        if isinstance(c.body, dict) and "chunk_size" in c.body and isinstance(c.body["chunk_size"], int)
    }
    assert 42 in emitted, f"Expected const value as a negative; got: {emitted}"


def test_negative_int64_boundary_below_minimum_is_invalid(ctx):
    # Integers just below the implied int64 minimum must be judged invalid, not rounded onto the bound.
    operation = body_operation(ctx, {"type": "integer", "format": "int64", "maximum": 100})

    cases = generate_cases(operation, GenerationMode.NEGATIVE)

    below_minimum = [case for case in cases if isinstance(case.body, int) and case.body < -(2**63)]
    assert below_minimum, "expected a below-minimum negative case"
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, cases=below_minimum)


DRAFT6_KEYWORD_SCHEMAS = [
    ({"type": "string", "const": "fixed"}, CoverageScenario.INVALID_ENUM_VALUE),
    (
        {"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}, "minProperties": 1},
        CoverageScenario.OBJECT_INVALID_PROPERTY_NAME,
    ),
]


@pytest.mark.parametrize(("body_schema", "scenario"), DRAFT6_KEYWORD_SCHEMAS, ids=["const", "propertyNames"])
def test_negative_draft6_keywords_not_negated_under_draft4(ctx, body_schema, scenario):
    # OAS 3.0 validates with Draft 4, which predates these keywords — their mutations are valid to the reference validator.
    cases = collect_coverage_cases(ctx, body_schema)
    assert scenario not in {c.meta.phase.data.scenario for c in cases}


@pytest.mark.parametrize(("body_schema", "scenario"), DRAFT6_KEYWORD_SCHEMAS, ids=["const", "propertyNames"])
def test_negative_draft6_keywords_negated_under_draft2020(ctx, body_schema, scenario):
    cases = collect_coverage_cases(ctx, body_schema, version="3.1.0")
    assert scenario in {c.meta.phase.data.scenario for c in cases}


def test_coverage_positive_template_with_enum_and_type_mismatch(ctx):
    # YAML parsing artifacts (e.g. bare `true`/`false`) in an enum with type:"string" must not
    # produce a schema-invalid template body.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["mode"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [True, False, "active"],
                }
            },
        },
        parameters=[
            {
                "in": "path",
                "name": "id",
                "required": True,
                "schema": {"type": "integer"},
            }
        ],
        path="/items/{id}",
        method="put",
    )

    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, cases=iter_cases(operation, GenerationMode.NEGATIVE))


def test_coverage_positive_template_required_property_absent_from_properties(ctx):
    # A required property not listed in `properties` must still appear in the template
    # body so the positive template is schema-valid when the negation is elsewhere.
    operation = body_operation(
        ctx,
        {
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
        },
        parameters=[
            {
                "in": "path",
                "name": "id",
                "required": True,
                "schema": {"type": "integer"},
            }
        ],
        path="/items/{id}",
        method="put",
    )

    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, cases=iter_cases(operation, GenerationMode.NEGATIVE))


def test_coverage_positive_template_skips_false_schema_property(ctx):
    # A property with boolean `false` schema means no value is valid — skip it rather than
    # assigning `0`, which would make the POSITIVE body schema-invalid.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "extra": False,
            },
        },
        parameters=[{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
        path="/items/{id}",
        method="patch",
    )

    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, cases=iter_cases(operation, GenerationMode.NEGATIVE))


def test_coverage_negative_string_length_nullable(ctx):
    # STRING_ABOVE_MAX_LENGTH / STRING_BELOW_MIN_LENGTH must produce a string, not `None`,
    # when the schema has `type: ["string", "null"]`.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"name": {"type": ["string", "null"], "maxLength": 10}},
        },
        path="/items",
    )

    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


def test_negative_min_length_emitted_when_pattern_requires_more_than_bound(ctx):
    # When `minLength > 1` AND `pattern` requires more chars than `minLength - 1`,
    # the bounded draw is unsatisfiable; fall back to truncation rather than dropping the negative.
    operation = body_operation(
        ctx,
        {
            "minLength": 2,
            "pattern": "^[A-Z][A-Za-z0-9-_+]+(?:/[A-Z][A-Za-z0-9-_+]+)*$",
            "type": "string",
        },
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)
    short_strings = [
        c.body for c in scenario_cases(cases, CoverageScenario.STRING_BELOW_MIN_LENGTH) if isinstance(c.body, str)
    ]
    assert short_strings, f"Expected a STRING_BELOW_MIN_LENGTH negative; got bodies: {[c.body for c in cases]}"
    for body in short_strings:
        assert len(body) < 2, f"Negative body {body!r} is not shorter than minLength=2"


def _assert_form_negatives_survive_stringification(operation):
    # Form encoding turns every value into a string; negatives must stay invalid in that shape.
    validator = body_validator(operation, "application/x-www-form-urlencoded")
    for case in iter_cases(operation, GenerationMode.NEGATIVE):
        if case.media_type != "application/x-www-form-urlencoded" or not isinstance(case.body, dict):
            continue
        if body_mode(case) != GenerationMode.NEGATIVE:
            continue
        wire = {key: str(value) for key, value in case.body.items()}
        assert not validator.is_valid(wire), f"NEGATIVE body is schema-valid after string coercion: {case.body!r}"


def test_coverage_negative_string_property_form_urlencoded_not_wire_identical(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"url": {"type": "string", "nullable": True}},
        },
        media_type="application/x-www-form-urlencoded",
        path="/items",
    )

    _assert_form_negatives_survive_stringification(operation)


def test_coverage_negative_string_property_xml_not_wire_identical(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"url": {"type": "string", "nullable": True}},
        },
        media_type="application/xml",
        path="/items",
    )

    validator = body_validator(operation, "application/xml")

    for case in iter_cases(operation, GenerationMode.NEGATIVE):
        if case.media_type != "application/xml":
            continue
        if body_mode(case) != GenerationMode.NEGATIVE or not isinstance(case.body, dict):
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
    operation = body_operation(
        ctx,
        {
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
        },
        path="/modify",
        method="patch",
    )
    validator = body_validator(operation)

    for case in iter_cases(operation, GenerationMode.POSITIVE):
        if case.media_type == "application/json" and body_mode(case) == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid for oneOf: {case.body!r}"


def test_coverage_positive_body_ref_with_pattern_and_length_constraints(ctx):
    # POSITIVE bodies must satisfy the anchored pattern even when the object body uses
    # `additionalProperties: false` alongside `$ref` properties with pattern/length constraints.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/TaskRequest"},
        path="/tasks",
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_coverage_positive_body_oneof_branch_required_field_missing_from_branch_properties(ctx):
    # POSITIVE bodies must satisfy the full schema when a oneOf branch requires a field
    # that is defined only in the parent schema's properties, not in the branch's own properties.
    operation = body_operation(
        ctx,
        {
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
        },
        path="/runs",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_coverage_negative_format_nullable(ctx):
    # INVALID_FORMAT must produce a non-null string when the schema has `type: ["string", "null"]`.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"email": {"type": ["string", "null"], "format": "email"}},
        },
        path="/items",
    )

    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


def test_coverage_form_urlencoded_primitive_body_negative_no_crash(ctx):
    operation = body_operation(
        ctx, {"type": "integer", "format": "int32"}, media_type="application/x-www-form-urlencoded", path="/convert"
    )

    cases = generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        case.as_curl_command()


def test_coverage_negative_string_above_max_length_invalid_when_pattern_quantifier_merged(ctx):
    # An unanchored quantifier like `{1,50}` doesn't prevent a 51-char string from passing
    # JSON Schema validation (partial match). The optimizer must anchor the pattern.
    operation = body_operation(
        ctx,
        {
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
        },
        path="/items",
    )
    above_max_cases = scenario_cases(
        generate_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.STRING_ABOVE_MAX_LENGTH
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, cases=above_max_cases, validate_formats=False)


def test_coverage_negative_max_length_preserved_when_pattern_has_inner_quantifier(ctx):
    operation = body_operation(
        ctx,
        {
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
        },
        path="/items",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=generate_cases, validate_formats=False)


def test_coverage_negative_max_length_preserved_when_outer_optional_group_has_variable_inner(ctx):
    # Optional group with variable inner: minLength absorbed (? to {1}) but maxLength unrepresentable.
    operation = body_operation(
        ctx,
        {
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
        },
        path="/items",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=generate_cases, validate_formats=False)


def test_coverage_negative_missing_required_with_additional_properties_schema(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "linkedServiceName": {"type": "object"},
            },
            "additionalProperties": {"type": "object"},
            "required": ["type", "linkedServiceName"],
        },
        path="/items",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=generate_cases, validate_formats=False)


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
    # A group matching one or two characters has no quantifier range that stops at exactly 100, so
    # the tuned schema settles below it and only the declared one can say whether a value is valid.
    operation = load_schema(
        ctx,
        parameters=[
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
        path="/items",
        method="get",
    )["/items"]["get"]
    query_param = next(p for p in operation.query if p.name == "name")
    validator = jsonschema_rs.validator_for(query_param.unoptimized_schema)

    cases = generate_cases(operation, GenerationMode.POSITIVE)
    positive_cases = [c for c in cases if c.query and "name" in c.query]
    assert len(positive_cases) > 0
    for case in positive_cases:
        assert validator.is_valid(case.query["name"]), f"Rewritten pattern corrupted: {case.query['name']!r}"


def test_coverage_positive_pattern_with_variable_suffix_not_overconstrained(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["lastName"],
            "properties": {
                "lastName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 30,
                    "pattern": r"^[a-zA-Z]+([ '-][a-zA-Z]+){0,2}\.?$",
                    "example": "Franklin",
                }
            },
        },
        path="/owners",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=generate_cases, validate_formats=False)


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


def test_coverage_positive_pattern_character_classes_stay_ascii(ctx):
    # Validators read `\d` and `\w` as ASCII only, so Unicode digits and letters are rejected.
    collect_coverage_cases(
        ctx,
        {
            "type": "object",
            "required": ["code"],
            "properties": {"code": {"type": "string", "pattern": r"^(a|b)[\w-]+$", "minLength": 20}},
        },
        positive=True,
    )


def test_negative_data_rejection_no_crash_with_large_dfa_pattern(ctx, response_factory):
    # \S{1,8192} exceeds jsonschema_rs's default DFA size limit; FANCY_REGEX_OPTIONS must be
    # passed when building the multi-element-array validator inside the check.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "query",
                "name": "configuration_token",
                "required": True,
                "schema": {"type": "string", "pattern": r"\S{1,8192}"},
            }
        ],
        path="/configuration",
        method="get",
    )["/configuration"]["get"]

    cases = generate_cases(operation, GenerationMode.NEGATIVE)

    response = response_factory.requests(status_code=200)
    ctx_check = check_context()

    for case in cases:
        with suppress(AcceptedNegativeData):
            negative_data_rejection(ctx_check, response, case)


NULLABLE_BINARY_MULTIPART_SCHEMA = {
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


def test_negative_data_rejection_no_false_positive_for_nullable_binary_multipart(ctx, response_factory):
    # `nullable: true` on a binary field converts to anyOf[{string/binary}, {null}].
    # Negating the null branch generates type mutations (dict, int, bool, etc.) that get
    # serialized to strings in multipart (str({}) -> "{}"), making them valid for the binary
    # field. is_valid_for_others must account for wire serialization so these aren't yielded.
    operation = body_operation(ctx, NULLABLE_BINARY_MULTIPART_SCHEMA, media_type="multipart/form-data", path="/upload")

    cases = generate_cases(operation, GenerationMode.NEGATIVE)

    response = response_factory.requests(status_code=200)
    ctx_check = check_context()

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


def test_negative_data_rejection_no_false_positive_for_multipart_body_type_mutations(ctx, response_factory):
    # Non-dict body values render as malformed multipart that lenient servers accept.
    operation = body_operation(ctx, NULLABLE_BINARY_MULTIPART_SCHEMA, media_type="multipart/form-data", path="/upload")

    cases = generate_cases(operation, GenerationMode.NEGATIVE)

    response = response_factory.requests(status_code=200)
    ctx_check = check_context()

    for case in cases:
        if isinstance(case.body, dict):
            continue
        assert negative_data_rejection(ctx_check, response, case) is None, (
            f"False positive: body {case.body!r} ({type(case.body).__name__})"
        )


def test_coverage_positive_body_nested_allof_inner_required_preserved(ctx):
    # Required fields from the second inner $ref (e.g. 'direction') must appear in POSITIVE bodies
    # when a oneOf branch resolves to allOf[{$ref: base}, {$ref: extension}].
    operation = body_operation(
        ctx,
        {
            "discriminator": {"propertyName": "product"},
            "oneOf": [{"$ref": "#/components/schemas/SMS"}],
        },
        path="/reports",
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
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_coverage_positive_body_string_type_with_empty_properties(ctx):
    # A property with type:string and properties:{} must generate a string value, not {}.
    # The properties keyword is irrelevant when type is not object.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "properties": {}},
            },
        },
        parameters=[{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
        path="/items/{id}",
        method="put",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, cases=iter_cases(operation, GenerationMode.NEGATIVE))


def test_coverage_positive_body_required_unsatisfiable_array_enum(ctx):
    # A required property nothing satisfies leaves no valid body; the template must not ship an incomplete one as
    # POSITIVE, even when the query parameter gives the phase something else to negate.
    operation = body_operation(
        ctx,
        {
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
        },
        parameters=[{"in": "query", "name": "version", "required": True, "schema": {"type": "integer"}}],
        path="/clients",
    )
    positives = [
        case.body for case in iter_cases(operation, *GenerationMode) if body_mode(case) == GenerationMode.POSITIVE
    ]
    assert positives == [], f"Unsatisfiable body emitted as POSITIVE: {positives!r}"


def test_coverage_no_recursion_for_allof_with_unmergeable_anyof_property(ctx):
    # Coverage must not recurse infinitely when canonicalish cannot merge allOf entries
    # (e.g. two object schemas with overlapping anyOf properties) and returns allOf with no type.
    operation = body_operation(
        ctx,
        {
            "allOf": [
                {
                    "type": "object",
                    "required": ["count"],
                    "properties": {
                        "count": {"anyOf": [{"const": None}, {"type": "integer", "minimum": 0}]},
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
        },
        path="/items",
        version="3.1.0",
    )
    # Must complete without RecursionError
    iter_cases(operation, GenerationMode.POSITIVE)


def test_coverage_positive_object_with_min_properties_no_required(ctx):
    # Object with minProperties:1 but no required fields must never yield {} as a positive body.
    body_schema = {
        "type": "object",
        "minProperties": 1,
        "properties": {
            "accountId": {"type": "string"},
            "domain": {"type": "string"},
        },
    }
    collect_coverage_cases(ctx, body_schema, positive=True)


def test_coverage_positive_object_no_required_collapsed_template_emits_empty_once(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "xml": {"name": "User"},
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
                "c": {"type": "string"},
                "d": {"type": "string"},
            },
        },
        path="/x",
    )
    cases = generate_cases(operation, GenerationMode.POSITIVE)
    empty_bodies = [c.body for c in cases if c.body == {}]
    assert len(empty_bodies) == 1, f"Expected one empty-body case, got {len(empty_bodies)}: {[c.body for c in cases]}"


def test_coverage_positive_oneof_branch_with_conflicting_root_type(ctx):
    # The root schema declares type:array but oneOf[0] declares type:object.
    # Positive coverage must never yield an object body — it can't satisfy both constraints.
    body_schema = {
        "type": "array",
        "items": {"type": "string"},
        "oneOf": [
            {
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                "required": ["items"],
            },
            {
                "type": "array",
                "items": {"type": "string"},
            },
        ],
    }
    collect_coverage_cases(ctx, body_schema, positive=True)


def test_coverage_positive_body_anyof_const_null_excluded_by_sibling_type(ctx):
    # When anyOf has a {const: null} branch but the sibling `type` constraint forbids null,
    # POSITIVE coverage must not yield null as a valid value for that property.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["count"],
            "properties": {
                "count": {
                    "anyOf": [{"const": None}, {"type": "integer", "minimum": 0}],
                    "type": "integer",
                    "minimum": 0,
                }
            },
        },
        path="/items",
        version="3.1.0",
    )
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True)


def test_coverage_positive_body_nested_required_unsatisfiable_field(ctx):
    # A nested required field nothing satisfies (pattern contradicts format) leaves no valid body at all.
    operation = body_operation(
        ctx,
        {
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
        },
        path="/items",
    )
    positives = [case.body for case in iter_cases(operation, GenerationMode.POSITIVE)]
    assert positives == [], f"Unsatisfiable body emitted as POSITIVE: {positives!r}"


def test_revalidation_preserves_negative_mode_for_format_violating_body(ctx):
    # A NEGATIVE body with a format-violating value ('' for a uuid field) must stay
    # NEGATIVE after body reassignment triggers _revalidate_metadata.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "iterationId": {
                    "type": "string",
                    "format": "uuid",
                    "nullable": True,
                }
            },
        },
        path="/items",
    )

    cases = iter_cases(operation, GenerationMode.NEGATIVE)

    target = next(
        (
            case
            for case in cases
            if isinstance(case.body, dict)
            and case.body.get("iterationId") == ""
            and body_mode(case) == GenerationMode.NEGATIVE
        ),
        None,
    )
    assert target is not None, "No NEGATIVE case with iterationId='' found"

    # Simulates what the engine does when auth or overrides reassign the body.
    target.body = target.body

    assert target.meta is not None
    assert target.meta.components[ParameterLocation.BODY].mode == GenerationMode.NEGATIVE


def test_negative_coverage_emits_invalid_format_for_uuid_body_property(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["orderId"],
            "properties": {"orderId": {"type": "string", "format": "uuid"}},
        },
        path="/items",
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)
    format_violators = [
        case
        for case in scenario_cases(cases, CoverageScenario.INVALID_FORMAT)
        if isinstance(case.body, dict) and "orderId" in case.body
    ]
    assert format_violators, "no INVALID_FORMAT case emitted for body property with format: uuid"
    value = format_violators[0].body["orderId"]
    with pytest.raises(ValueError):
        uuid.UUID(value)


def test_coverage_form_urlencoded_filters_primitives_with_bundled_ref(ctx):
    # Every NEGATIVE form-urlencoded body must remain schema-invalid after string coercion.
    operation = body_operation(
        ctx,
        {
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
        },
        media_type="application/x-www-form-urlencoded",
        path="/t",
        components={
            "schemas": {
                "Nested": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Nested"}},
                }
            }
        },
    )
    _assert_form_negatives_survive_stringification(operation)


def test_coverage_form_urlencoded_filters_nested_wire_identical_mutations(ctx):
    # Every NEGATIVE form-urlencoded body must remain schema-invalid after string coercion.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "input": {
                    "anyOf": [
                        {
                            "oneOf": [
                                {"type": "string", "maxLength": 10000},
                                {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["role"],
                                        "properties": {
                                            "role": {
                                                "type": "string",
                                                "enum": ["user", "assistant"],
                                            }
                                        },
                                    },
                                },
                            ]
                        },
                        {"type": "null"},
                    ]
                }
            },
        },
        media_type="application/x-www-form-urlencoded",
        path="/t",
    )
    _assert_form_negatives_survive_stringification(operation)


def test_coverage_array_above_max_items_with_complex_items_schema(ctx):
    # Every NEGATIVE body must fail schema validation.
    operation = body_operation(
        ctx,
        {
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
        },
        path="/items",
    )
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False)


def test_coverage_array_above_max_items_with_draft_mismatch_sibling(ctx):
    # When a sibling keyword breaks the auto-detected validator (e.g. `exclusiveMinimum: true`),
    # the `ARRAY_ABOVE_MAX_ITEMS` mutation must still produce a body whose target array exceeds
    # maxItems — spec-supplied examples whose arrays fit within bounds must not slip through.
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "examples": {"good": {"value": {"t": [0.5, 0.9], "k": [0.1, 0.2]}}},
                    "schema": {
                        "type": "object",
                        "required": ["t"],
                        "properties": {
                            "t": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 3,
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "k": {
                                "type": "array",
                                "minItems": 2,
                                "items": {"type": "number", "minimum": 0, "exclusiveMinimum": True},
                            },
                        },
                    },
                }
            },
        },
        path="/r",
    )["/r"]["post"]
    for case in scenario_cases(iter_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.ARRAY_ABOVE_MAX_ITEMS):
        body_t = case.body.get("t") if isinstance(case.body, dict) else None
        assert body_t is not None and len(body_t) > 3, (
            f"ARRAY_ABOVE_MAX_ITEMS mutation produced a body within bounds: {case.body!r}"
        )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_array_items_enum_entries_violating_item_schema(cli, snapshot_cli, ctx):
    # No `enum` entry is a string, so nothing may be sent as valid data for a server that enforces the schema.
    paths = {
        "/tags": {
            "post": {
                "operationId": "createTags",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": [1, 2]}}
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/tags", methods=["POST"])
    def create_tags():
        data = request.get_json(silent=True)
        if not isinstance(data, list) or not data or not all(isinstance(item, str) for item in data):
            return "", 400
        return "", 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c positive_data_acceptance",
        )
        == snapshot_cli
    )


def test_undeclared_method_probes_dedup_across_operations(ctx):
    # Each (path, unexpected_method) pair is emitted once across all declared operations on the path.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                method: {"responses": {"200": {"description": "OK"}}} for method in ("get", "post", "put", "delete")
            },
        },
    )
    unexpected_methods = {"options", "patch", "trace", "query"}

    seen: list[tuple[str, str]] = []
    seen_dedup: set[tuple[str, str]] = set()
    for declared in ("GET", "POST", "PUT", "DELETE"):
        for case in iter_coverage_cases(
            operation=schema["/items"][declared],
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=unexpected_methods,
            generation_config=schema.config.generation,
            unexpected_methods_seen=seen_dedup,
        ):
            if case.meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD:
                seen.append((case.operation.path, case.method))

    assert sorted(seen) == sorted([("/items", method.upper()) for method in unexpected_methods])


@pytest.mark.parametrize(
    "consumes",
    [["*/*"], ["*/*", "application/json"], ["application/xml", "*/*"]],
    ids=["wildcard-only", "wildcard-then-json", "xml-then-wildcard"],
)
def test_wildcard_consumes_picks_concrete_media_type(ctx, consumes):
    # Real clients never send Content-Type: */*; coverage must pick a concrete media type.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": consumes,
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/foo"]["POST"]
    media_types = {
        case.media_type for case in iter_cases(operation, GenerationMode.POSITIVE) if case.body is not NOT_SET
    }
    assert "*/*" not in media_types, f"Wildcard leaked into Content-Type: {media_types}"
    assert media_types, "expected at least one body-carrying case"
    concrete = [m for m in consumes if m != "*/*"]
    if concrete:
        assert media_types <= set(concrete), f"Unexpected media types: {media_types}"
    else:
        assert media_types == {"application/json"}


def test_multipart_body_with_binary_ref_completes_coverage(ctx):
    # Multipart bodies whose schema referenced a nested $ref aborted with a validator error mid-iteration.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Upload"},
        media_type="multipart/form-data",
        path="/upload",
        version="3.0.0",
        components={
            "schemas": {
                "Upload": {
                    "nullable": True,
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "owner": {"$ref": "#/components/schemas/Owner"},
                    },
                },
                "Owner": {"type": "object", "properties": {"id": {"type": "string"}}},
            }
        },
    )
    config = SanitizationConfig(enabled=False)
    count = 0
    for case in iter_cases(operation, *GenerationMode):
        prepare_request(case, headers=None, config=config)
        count += 1
    assert count > 0


def test_explicit_content_type_header_does_not_collide_with_body_coverage(ctx):
    # When CT is declared as an explicit header parameter, body cases must keep CT pinned to the
    # body's media type, and CT-mutation cases must not also carry a body (the two sweeps are independent).
    operation = body_operation(
        ctx,
        {"type": "object", "properties": {"email": {"type": "string"}}},
        parameters=[
            {
                "name": "Content-Type",
                "in": "header",
                "type": "string",
                "enum": ["application/json", "application/xml"],
                "default": "application/json",
            }
        ],
        path="/forgot",
        version="2.0",
    )
    body_cases_cts = set()
    ct_mutation_bodies = []
    for case in iter_cases(operation, GenerationMode.POSITIVE) + iter_cases(operation, GenerationMode.NEGATIVE):
        headers = case.headers or {}
        ct = headers.get("Content-Type")
        param_loc = case.meta.phase.data.parameter_location
        param_name = case.meta.phase.data.parameter
        is_ct_mutation = param_loc == ParameterLocation.HEADER and (param_name or "").lower() == "content-type"
        if is_ct_mutation:
            ct_mutation_bodies.append(case.body)
        elif case.body is not NOT_SET:
            assert ct == "application/json", f"body case got Content-Type={ct!r}, expected 'application/json'"
            body_cases_cts.add(ct)
    assert body_cases_cts == {"application/json"}, f"expected body cases pinned to JSON, got {body_cases_cts}"
    assert ct_mutation_bodies, "expected Content-Type mutation cases to be generated"
    assert all(b is NOT_SET for b in ct_mutation_bodies), (
        f"CT-mutation cases should not carry a body, got: {ct_mutation_bodies}"
    )


def test_recursive_ref_negative_descends_past_self_reference(ctx):
    # Self-referential arms must receive a type-violating element at the inner-`$ref` position,
    # not just be skipped when the negative generator hits the recursion boundary.
    operation = body_operation(
        ctx,
        {"$ref": "#/definitions/Filter"},
        path="/filter",
        version="2.0",
        definitions={
            "Filter": {
                "type": "object",
                "properties": {
                    "and": {
                        "type": "array",
                        "minItems": 2,
                        "items": {"$ref": "#/definitions/Filter"},
                    },
                    "or": {
                        "type": "array",
                        "minItems": 2,
                        "items": {"$ref": "#/definitions/Filter"},
                    },
                    "not": {"$ref": "#/definitions/Filter"},
                    "leaf": {"type": "string"},
                },
            },
        },
    )
    validator = body_validator(operation)

    negatives = [case for case in iter_cases(operation, GenerationMode.NEGATIVE) if case.body is not NOT_SET]
    invalid_items_for: set[str] = set()
    invalid_not = False
    for case in negatives:
        body = case.body
        if not isinstance(body, dict) or validator.is_valid(body):
            continue
        for arm in ("and", "or"):
            arm_value = body.get(arm)
            if not isinstance(arm_value, list) or len(arm_value) < 2:
                continue
            if any(not isinstance(item, dict) for item in arm_value):
                invalid_items_for.add(arm)
        not_value = body.get("not")
        if not_value is not None and not isinstance(not_value, dict):
            invalid_not = True
    assert invalid_items_for == {"and", "or"}, f"missing arm items violations: {invalid_items_for}"
    assert invalid_not, "missing 'not' arm type violation"


def test_unsatisfiable_items_schema_falls_back_to_single_item_negative(ctx):
    # When the items schema can't produce a valid filler (here `{"not": {}}` matches nothing),
    # the negative-items branch falls back to a single-item array rather than emitting nothing.
    operation = body_operation(ctx, {"type": "array", "minItems": 2, "items": {"not": {}}}, path="/items")
    bodies = [case.body for case in iter_cases(operation, GenerationMode.NEGATIVE) if case.body is not NOT_SET]
    single_item_arrays = [b for b in bodies if isinstance(b, list) and len(b) == 1]
    assert single_item_arrays, f"fallback should emit single-item arrays, got bodies: {bodies}"


def _tool_branch_property(tag_keyword, value):
    # `None` produces a bare string property so the pin falls back to the schema name.
    if tag_keyword is None:
        return {"type": "string"}
    if tag_keyword == "enum":
        return {"type": "string", "enum": [value]}
    return {"type": "string", "const": value}


def _tool_components(tag_keyword, *, mapping=None):
    discriminator: dict = {"propertyName": "type"}
    if mapping is not None:
        discriminator["mapping"] = mapping
    return {
        "schemas": {
            "Tool": {
                "discriminator": discriminator,
                "oneOf": [
                    {"$ref": "#/components/schemas/FunctionTool"},
                    {"$ref": "#/components/schemas/WebSearchTool"},
                ],
            },
            "FunctionTool": {
                "type": "object",
                "required": ["type", "name"],
                "properties": {
                    "type": _tool_branch_property(tag_keyword, "function"),
                    "name": {"type": "string"},
                },
            },
            "WebSearchTool": {
                "type": "object",
                "required": ["type", "query"],
                "properties": {
                    "type": _tool_branch_property(tag_keyword, "web_search"),
                    "query": {"type": "string"},
                },
            },
        },
    }


def _discriminator_positive_bodies(operation):
    return [case.body for case in iter_cases(operation, GenerationMode.POSITIVE) if isinstance(case.body, dict)]


@pytest.mark.parametrize(
    ("tag_keyword", "expected_tags"),
    [
        ("enum", {"function", "web_search"}),
        ("const", {"function", "web_search"}),
        (None, {"FunctionTool", "WebSearchTool"}),
    ],
)
def test_discriminator_pin_uses_branch_value_when_available(ctx, tag_keyword, expected_tags):
    # const/enum on the branch supplies the literal tag; absence falls back to the schema name.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["tools"],
            "properties": {"tools": {"$ref": "#/components/schemas/Tool"}},
        },
        path="/r",
        components=_tool_components(tag_keyword),
    )
    bodies = _discriminator_positive_bodies(operation)
    tags = {body["tools"]["type"] for body in bodies if isinstance(body.get("tools"), dict) and "type" in body["tools"]}
    assert tags == expected_tags, f"expected {expected_tags}; got tags={tags}, bodies={bodies}"


def test_discriminator_polymorphic_items_array_covers_each_branch(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["tools"],
            "properties": {
                "tools": {"type": "array", "items": {"$ref": "#/components/schemas/Tool"}},
            },
        },
        path="/r",
        components=_tool_components("enum"),
    )
    bodies = _discriminator_positive_bodies(operation)
    tags = {
        item["type"]
        for body in bodies
        if isinstance(body.get("tools"), list)
        for item in body["tools"]
        if isinstance(item, dict) and "type" in item
    }
    assert tags == {"function", "web_search"}, f"expected both branches; got tags={tags}, bodies={bodies}"


def test_discriminator_explicit_mapping_overrides_branch_const(ctx):
    # The mapping pins FunctionTool to "f-tag" (conflicts with its const "function" -> unsatisfiable),
    # and WebSearchTool to "web_search" (matches its const). If the mapping correctly wins over the
    # branch const, only the WebSearchTool branch is generatable.
    components = _tool_components(
        "const",
        mapping={
            "f-tag": "#/components/schemas/FunctionTool",
            "web_search": "#/components/schemas/WebSearchTool",
        },
    )
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["tools"],
            "properties": {"tools": {"$ref": "#/components/schemas/Tool"}},
        },
        path="/r",
        components=components,
    )
    bodies = _discriminator_positive_bodies(operation)
    tags = {body["tools"]["type"] for body in bodies if isinstance(body.get("tools"), dict) and "type" in body["tools"]}
    assert tags == {"web_search"}, f"mapping must override branch const; got tags={tags}, bodies={bodies}"


def test_negative_coverage_violates_int64_format_bounds(ctx):
    # The range implied by `format: int64` must reach negative generation as real bounds,
    # so out-of-range integers stay covered as boundary violations instead of positive data.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"value": {"type": "integer", "format": "int64"}},
            "required": ["value"],
        },
        path="/x",
        version="3.1.0",
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)

    violations = {
        case.meta.phase.data.scenario: case.body["value"]
        for case in cases
        if isinstance(case.body, dict) and isinstance(case.body.get("value"), int)
    }
    assert violations[CoverageScenario.VALUE_ABOVE_MAXIMUM] == 2**63
    assert violations[CoverageScenario.VALUE_BELOW_MINIMUM] == -(2**63) - 1
    assert all(case.meta.generation.mode == GenerationMode.NEGATIVE for case in cases)


def test_coverage_recursive_body_is_generated(ctx):
    # A pointer back into the value has no unrolled form, so the value is built from the pointer
    # itself rather than from a copy of what it names.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Node"},
        path="/nodes",
        components={
            "schemas": {
                "Node": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}, "child": {"$ref": "#/components/schemas/Node"}},
                }
            }
        },
    )
    assert any("child" in body for body in assert_bodies(operation, GenerationMode.POSITIVE, valid=True))


def test_coverage_recursion_around_a_node_that_cannot_be_built(ctx):
    # Two formats at once is a conjunction neither generator spells, so the only values left are the
    # ones `minProperties` alone admits — the pointer around it must not derail them.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Node"},
        path="/nodes",
        components={
            "schemas": {
                "Node": {
                    "type": "object",
                    "minProperties": 1,
                    "properties": {
                        "stamp": {
                            "allOf": [{"type": "string", "format": "ipv4"}, {"type": "string", "format": "date"}]
                        },
                        "child": {"$ref": "#/components/schemas/Node"},
                    },
                }
            }
        },
    )
    for body in assert_bodies(operation, GenerationMode.POSITIVE, valid=True):
        assert "stamp" not in body, body


def test_mutually_recursive_pointers_do_not_multiply_the_walk(ctx):
    # Every pointer doubling on its own multiplies the paths through a cycle graph; ending the walk
    # at the first doubling keeps the position that points back covered without the product.
    names = [f"Node{index}" for index in range(4)]
    operation = body_operation(
        ctx,
        {"$ref": f"#/components/schemas/{names[0]}"},
        path="/nodes",
        components={
            "schemas": {
                name: {
                    "type": "object",
                    "properties": {
                        **{f"to{other}": {"$ref": f"#/components/schemas/{other}"} for other in names if other != name},
                        "leaf": {"type": "string"},
                    },
                }
                for name in names
            }
        },
    )

    assert len(iter_cases(operation, GenerationMode.NEGATIVE)) < 1000


def test_pointer_reached_twice_still_carries_what_it_names(ctx):
    # The envelope pointer reappears below itself, and a nested value that ignores what it names
    # is one nothing can accept - the position has to be built from both, not judged after.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Connection"},
        path="/connections",
        components={
            "schemas": {
                "Resource": {
                    "type": "object",
                    "required": ["location"],
                    "properties": {"location": {"type": "string"}},
                },
                "Api": {
                    "allOf": [{"$ref": "#/components/schemas/Resource"}],
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
                "Connection": {
                    "allOf": [{"$ref": "#/components/schemas/Resource"}],
                    "type": "object",
                    "properties": {"api": {"$ref": "#/components/schemas/Api"}},
                },
            }
        },
    )
    assert any("api" in body for body in assert_bodies(operation, GenerationMode.POSITIVE, valid=True))


def test_ref_parameter_schema_keeps_combination_coverage(ctx):
    # Parameter combinations are generated from a synthesized schema, where a `$ref` still has to resolve.
    enum = {"type": "string", "enum": ["a", "b"]}

    def descriptions(first, second):
        operation = load_schema(
            ctx,
            parameters=[
                {"name": "q", "in": "query", "required": True, "schema": first},
                {"name": "r", "in": "query", "required": False, "schema": second},
            ],
            path="/r",
            method="get",
            components={"schemas": {"E": enum}},
        )["/r"]["GET"]
        return sorted(case.meta.phase.data.description for case in iter_cases(operation, *GenerationMode))

    reference = {"$ref": "#/components/schemas/E"}
    assert descriptions(
        {"type": "object", "properties": {"t": reference}, "required": ["t"], "additionalProperties": False},
        reference,
    ) == descriptions(
        {"type": "object", "properties": {"t": enum}, "required": ["t"], "additionalProperties": False},
        enum,
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_allow_header_conformance(ctx, cli, snapshot_cli):
    # Flask builds `Allow` from its own routing table, so a documented but unimplemented method is missing from it.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"201": {"description": "Created"}}},
            }
        }
    )

    @app.route("/items", methods=["GET"])
    def items():
        return jsonify([])

    assert (
        cli.run_openapi_app(
            app,
            "--checks=allow_header_conformance",
            "--phases=coverage",
            "--mode=negative",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "item_schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "items": {"type": "string"}, "minItems": 2},
        {"type": "array", "items": {"type": "string"}, "maxItems": 0},
        {"type": "array", "items": {"type": "string"}, "example": []},
        {"type": "object", "properties": {"a": {"type": "string"}}},
        {"type": "object", "additionalProperties": {"type": "string"}},
    ],
    ids=["unbounded", "min-items", "max-items-zero", "empty-example", "object", "free-form-object"],
)
def test_container_path_parameter_never_blanks_the_path_segment(ctx, item_schema):
    # A blank segment collapses the URL onto another operation, so the case tests something else.
    operation = load_schema(
        ctx, parameters=[{"name": "v", "in": "path", "required": True, "schema": item_schema}], path="/p/{v}"
    )["/p/{v}"]["post"]
    for case in iter_cases(operation, *GenerationMode):
        assert case.formatted_path != "/p/", f"blank path segment from {case.path_parameters!r}"


@pytest.mark.parametrize("location", ["header", "cookie"])
@pytest.mark.parametrize("keyword", ["example", "default"])
@pytest.mark.parametrize("value", ["application/json", "en-US", "application/vnd.github.v3+json"])
def test_spec_hint_with_non_alphanumeric_characters(ctx, location, keyword, value):
    operation = load_schema(
        ctx,
        parameters=[
            {"in": location, "name": "X-Sample", "required": True, "schema": {"type": "string", keyword: value}}
        ],
    )["/foo"]["POST"]
    assert value in {
        getattr(case, LOCATION_TO_CONTAINER[location]).get("X-Sample")
        for case in iter_cases(operation, GenerationMode.POSITIVE)
    }


@pytest.mark.parametrize("max_length", [65535, 2147483647])
def test_required_string_with_max_length_beyond_generation_buffer(ctx, max_length):
    operation = load_schema(
        ctx,
        parameters=[
            {"in": "query", "name": "key", "required": True, "schema": {"type": "string", "maxLength": max_length}}
        ],
    )["/foo"]["POST"]
    assert any("key" in case.query for case in iter_cases(operation, GenerationMode.POSITIVE))


def test_object_query_parameter_yields_no_duplicate_requests(ctx):
    # Non-dict values collapse to `name=` on the wire, so every type violation repeats the positive request.
    operation = load_schema(
        ctx,
        parameters=[{"in": "query", "name": "filter", "required": False, "schema": {"type": "object"}}],
        method="get",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, *GenerationMode)] == [{"filter": ""}]


def test_two_object_query_parameters_yield_no_duplicate_requests(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {"in": "query", "name": "filter", "required": False, "schema": {"type": "object"}},
            {"in": "query", "name": "sort_by", "required": False, "schema": {"type": "object"}},
        ],
        method="get",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, *GenerationMode)] == [
        {"sort_by": "", "filter": ""},
        {"filter": ""},
        {"x-schemathesis-unknown-property": "42", "filter": ""},
        {"sort_by": ""},
        {"x-schemathesis-unknown-property": "42", "sort_by": ""},
    ]


def test_array_query_parameter_yields_no_duplicate_requests(ctx):
    # A one-item list and the bare value put the same `ids=` on the wire.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "query",
                "name": "ids",
                "required": False,
                "style": "form",
                "explode": True,
                "schema": {"type": "array", "items": {"type": "integer"}},
            }
        ],
        method="get",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, *GenerationMode)] == [
        {"ids": ["0"]},
        {"ids": []},
        {"ids": "0.5"},
        {"ids": "true"},
        {"ids": "null"},
        {"ids": "AAA"},
        {"ids": [["null", "null"]]},
    ]


def test_empty_array_query_parameter_yields_no_duplicate_requests(ctx):
    # An empty list sends nothing, so combinations differing only by it hit the same URL.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "query",
                "name": name,
                "required": False,
                "style": "form",
                "explode": True,
                "schema": {"type": "array", "items": {"type": "string", "enum": ["x"]}},
            }
            for name in ("a", "b")
        ],
        method="get",
    )["/foo"]["GET"]
    config = SanitizationConfig(enabled=False)
    urls = [prepare_request(case, headers=None, config=config).url for case in iter_cases(operation, *GenerationMode)]
    assert sorted(urls) == sorted(set(urls))


def test_body_and_parameter_cases_yield_no_duplicate_requests(ctx):
    # The body case already carries the template's empty header, so the header's own positive repeats it.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string", "example": "app"}},
        },
        parameters=[
            {"in": "header", "name": "X-Key", "required": False, "schema": {"type": "string", "nullable": True}}
        ],
    )
    assert [(dict(case.headers), case.body) for case in iter_cases(operation, GenerationMode.POSITIVE)] == [
        ({"X-Key": ""}, {"name": "app"}),
        ({"X-Key": "null"}, {"name": "app"}),
    ]


def test_unbuildable_optional_property_does_not_erase_positive_cases(ctx):
    # One property nothing can satisfy must not wipe out every positive case for the whole body.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "token": {
                    "type": "string",
                    "pattern": ".*",
                    "minLength": 0,
                    "maxLength": 2147483647,
                },
            },
        },
    )
    assert [case.body for case in iter_cases(operation, GenerationMode.POSITIVE)] == [
        {"name": ""},
        {"name": "", "token": ""},
    ]


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("title", "Payload"),
        ("deprecated", True),
        ("externalDocs", {"url": "https://example.com"}),
        ("xml", {"name": "payload"}),
    ],
)
def test_annotation_keyword_does_not_erase_positive_cases(ctx, keyword, value):
    # Keywords that describe an object rather than constrain it must not change what it generates.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            keyword: value,
            "required": ["token"],
            "properties": {
                "token": {
                    "type": "string",
                    "pattern": ".*",
                    "minLength": 0,
                    "maxLength": 2147483647,
                },
            },
        },
    )
    assert [case.body for case in iter_cases(operation, GenerationMode.POSITIVE)] == [{"token": ""}]


def test_querystring_parameter_is_not_duplicated(ctx):
    # A `querystring` parameter serializes its whole content as the raw query, so repeating it means nothing.
    operation = load_schema(
        ctx,
        [
            {
                "name": "raw",
                "in": "querystring",
                "required": True,
                "content": {
                    "application/x-www-form-urlencoded": {
                        "schema": {"type": "object", "properties": {"a": {"type": "string"}}}
                    }
                },
            },
        ],
        version="3.2.0",
    )["/foo"]["post"]
    assert [
        case.meta.phase.data.parameter
        for case in collect_cases(operation, GenerationMode.NEGATIVE, generate_duplicate_query_parameters=True)
        if case.meta.phase.data.scenario == CoverageScenario.DUPLICATE_PARAMETER
    ] == []


@pytest.mark.parametrize(
    ("minimum", "keeps_example"),
    [(5, False), (1, True)],
    ids=["contradicted", "compatible"],
)
def test_parameter_example_is_dropped_when_an_inferred_bound_contradicts_it(ctx, minimum, keeps_example):
    operation = load_schema(
        ctx,
        [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}, "example": "ab"}],
    )["/foo"]["post"]
    store = ErrorFeedbackStore()
    store.record(
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.QUERY,
            parameter_path=("q",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message=f"size must be at least {minimum}",
            payload=SizeBoundPayload(min=minimum, max=None),
        )
    )
    values = [case.query["q"] for case in iter_cases(operation, GenerationMode.POSITIVE, error_feedback=store)]
    assert values and all(len(value) >= minimum for value in values), values
    assert ("ab" in values) is keeps_example


@pytest.mark.parametrize(
    ("minimum", "keeps_example"),
    [(5, False), (1, True)],
    ids=["contradicted", "compatible"],
)
def test_body_example_is_dropped_when_an_inferred_bound_contradicts_it(ctx, minimum, keeps_example):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "example": {"name": "ab"},
        },
    )
    store = ErrorFeedbackStore()
    store.record(
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message=f"size must be at least {minimum}",
            payload=SizeBoundPayload(min=minimum, max=None),
        )
    )
    bodies = [
        case.body
        for case in iter_cases(operation, GenerationMode.POSITIVE, error_feedback=store)
        if case.body is not NOT_SET
    ]
    assert bodies and all(len(body["name"]) >= minimum for body in bodies), bodies
    assert ({"name": "ab"} in bodies) is keeps_example


def test_multipart_template_body_built_from_custom_property_encodings(ctx):
    # A property with a registered `encoding.contentType` draws from that strategy; other required
    # properties get plain fillers so the multipart template stays complete.
    schemathesis.openapi.media_type("image/png", st.just(b"\x89PNG"))
    operation = load_schema(
        ctx,
        parameters=[{"name": "q", "in": "query", "schema": {"type": "string", "enum": ["a", "b"]}}],
        request_body={
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "note": {"type": "string"},
                            "name": {"type": "string"},
                        },
                        "required": ["file", "name"],
                    },
                    "encoding": {"file": {"contentType": "image/png"}},
                }
            },
        },
    )["/foo"]["post"]
    bodies = [case.body for case in iter_cases(operation, GenerationMode.POSITIVE)]
    assert {"file": b"\x89PNG", "name": ""} in bodies, bodies[:5]


def test_multipart_property_with_unregistered_content_type_falls_back_to_schema_generation(ctx):
    # An `encoding.contentType` with no registered strategy contributes nothing custom;
    # the property is generated from its schema like any other.
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "enum": ["from-schema"]}},
                        "required": ["file"],
                    },
                    "encoding": {"file": {"contentType": "application/x-unregistered"}},
                }
            },
        },
    )["/foo"]["post"]
    bodies = [case.body for case in iter_cases(operation, GenerationMode.POSITIVE)]
    assert {"file": "from-schema"} in bodies, bodies[:5]


def test_each_custom_media_type_alternative_yields_its_own_body(ctx):
    schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
    schemathesis.openapi.media_type("image/jpeg", st.just(b"\xff\xd8jpeg"))
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    )["/foo"]["post"]
    assert [(case.media_type, case.body) for case in iter_cases(operation, GenerationMode.POSITIVE)] == [
        ("application/pdf", b"%PDF-1.4"),
        ("image/jpeg", b"\xff\xd8jpeg"),
    ]


def test_combination_cases_deduplicate_on_wire_form(ctx):
    # 'x-token' and 'X-Token' collapse into one header on the wire; combination cases that
    # repeat an already-emitted request are suppressed.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "x-token",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "enum": ["secret"]},
                        },
                        {
                            "name": "X-Token",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string", "enum": ["secret"]},
                        },
                        {
                            "name": "X-Other",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string", "enum": ["other"]},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/items"]["GET"]
    stream = [
        (
            case.meta.phase.data.scenario.value,
            case.meta.generation.mode.value,
            case.meta.phase.data.parameter,
            dict(case.headers or {}),
        )
        for case in iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
    ]
    assert stream == [
        ("default_positive_test", "positive", None, {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("invalid_enum_value", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Token", {"X-Token": "0", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Token", {"X-Token": "0.5", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Token", {"X-Token": "true", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Token", {"X-Token": "null", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Token", {"X-Token": "null,null", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Token", {"X-Token": "{}", "X-Other": "other"}),
        ("invalid_enum_value", "negative", "X-Token", {"X-Token": "AAA", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Other", {"X-Token": "secret", "X-Other": "0"}),
        ("incorrect_type", "negative", "X-Other", {"X-Token": "secret", "X-Other": "0.5"}),
        ("incorrect_type", "negative", "X-Other", {"X-Token": "secret", "X-Other": "true"}),
        ("incorrect_type", "negative", "X-Other", {"X-Token": "secret", "X-Other": "null"}),
        ("incorrect_type", "negative", "X-Other", {"X-Token": "secret", "X-Other": "null,null"}),
        ("incorrect_type", "negative", "X-Other", {"X-Token": "secret", "X-Other": "{}"}),
        ("invalid_enum_value", "negative", "X-Other", {"X-Token": "secret", "X-Other": "AAA"}),
        ("missing_parameter", "negative", "x-token", {"X-Token": "secret", "X-Other": "other"}),
        ("object_only_required", "positive", None, {"x-token": "secret"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "0"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "0.5"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "true"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "null"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "null,null"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "{}"}),
        ("invalid_enum_value", "negative", "x-token", {"x-token": "AAA"}),
        (
            "object_unexpected_properties",
            "negative",
            None,
            {"x-token": "secret", "x-schemathesis-unknown-property": "42"},
        ),
        ("object_required_and_optional", "positive", None, {"x-token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"X-Other": "other", "x-token": "0"}),
        ("incorrect_type", "negative", "x-token", {"X-Other": "other", "x-token": "0.5"}),
        ("incorrect_type", "negative", "x-token", {"X-Other": "other", "x-token": "true"}),
        ("incorrect_type", "negative", "x-token", {"X-Other": "other", "x-token": "null"}),
        ("incorrect_type", "negative", "x-token", {"X-Other": "other", "x-token": "null,null"}),
        ("incorrect_type", "negative", "x-token", {"X-Other": "other", "x-token": "{}"}),
        ("invalid_enum_value", "negative", "x-token", {"X-Other": "other", "x-token": "AAA"}),
        ("incorrect_type", "negative", "X-Other", {"X-Other": "0", "x-token": "secret"}),
        ("incorrect_type", "negative", "X-Other", {"X-Other": "0.5", "x-token": "secret"}),
        ("incorrect_type", "negative", "X-Other", {"X-Other": "true", "x-token": "secret"}),
        ("incorrect_type", "negative", "X-Other", {"X-Other": "null", "x-token": "secret"}),
        ("incorrect_type", "negative", "X-Other", {"X-Other": "null,null", "x-token": "secret"}),
        ("incorrect_type", "negative", "X-Other", {"X-Other": "{}", "x-token": "secret"}),
        ("invalid_enum_value", "negative", "X-Other", {"X-Other": "AAA", "x-token": "secret"}),
        (
            "object_unexpected_properties",
            "negative",
            None,
            {"X-Other": "other", "x-token": "secret", "x-schemathesis-unknown-property": "42"},
        ),
        ("object_required_and_optional", "positive", None, {"X-Token": "secret"}),
    ]
