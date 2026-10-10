import json
import re
import uuid
from dataclasses import dataclass
from unittest.mock import ANY

import jsonschema_rs
import pytest
from flask import Flask, jsonify, request
from hypothesis import strategies as st
from requests import Request

import schemathesis
from schemathesis import GenerationMode
from schemathesis.checks import not_a_server_error
from schemathesis.config import SanitizationConfig
from schemathesis.core import NOT_SET
from schemathesis.core.error_feedback.store import (
    ErrorFeedbackStore,
    Observation,
    ObservationKind,
    SizeBoundPayload,
)
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import AcceptedNegativeData, ContentTypeServerError, FailureGroup
from schemathesis.core.parameters import LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.generation.meta import CONTENT_TYPE_PROBES, REQUEST_SHAPE_PROBES, CoverageScenario, TestPhase
from schemathesis.specs.openapi.checks import negative_data_rejection
from schemathesis.specs.openapi.coverage._wire import quote_path_parameter
from schemathesis.transport.prepare import prepare_request
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from test.coverage.helpers import (
    DEFAULT_RESPONSES,
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
    generate_hooked_cases,
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
    # Missing required body
    {"query": {"q1": "0.5", "q2": "0"}, "headers": {"h1": "0.5", "h2": "true"}},
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
    # Missing required body
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}},
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


@pytest.mark.parametrize("declared_type", ["integer", ["integer", "null"]], ids=["single", "list"])
def test_non_numeric_default_of_integer_is_not_sent(ctx, declared_type):
    parameters = [{"name": "q", "in": "query", "schema": {"type": declared_type, "default": b"y"}}]
    operation = load_schema(ctx, parameters)["/foo"]["post"]
    cases = collect_cases(operation, GenerationMode.POSITIVE)
    assert cases
    assert all(case.query.get("q") != b"y" for case in cases)


BINARY_KEYWORDS_SCHEMA = """
openapi: 3.0.2
info: {title: t, version: "1"}
paths:
  /query:
    get:
      parameters:
        - {name: day, in: query, required: true, schema: {type: string, format: date, default: !!binary enp6}}
      responses: {"200": {description: OK}}
  /header:
    get:
      parameters:
        - {name: X-Count, in: header, required: true, schema: {type: integer, default: !!binary eA==}}
      responses: {"200": {description: OK}}
  /form:
    post:
      requestBody:
        required: true
        content:
          application/x-www-form-urlencoded:
            schema:
              type: object
              required: [value]
              properties: {value: {type: string, default: !!binary enp6}}
      responses: {"200": {description: OK}}
  /json:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [value]
              properties: {value: {type: string, example: !!binary enp6, enum: [!!binary enp6, valid]}}
      responses: {"200": {description: OK}}
"""


def test_yaml_binary_keyword_values_do_not_break_coverage(ctx, tmp_path, app_runner):
    path = tmp_path / "openapi.yaml"
    path.write_text(BINARY_KEYWORDS_SCHEMA)
    app = Flask(__name__)
    app.add_url_rule("/<path:anything>", "any", lambda anything: ("", 200), methods=["GET", "POST"])
    schema = schemathesis.openapi.from_path(path)
    schema.config.update(base_url=app_runner.openapi_url(app, path=""))
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage"])
    errors = [
        event.value
        for event in schemathesis.engine.from_schema(schema).execute()
        if isinstance(event, schemathesis.engine.events.NonFatalError)
    ]
    assert errors == []


@pytest.mark.parametrize(
    "body_schema",
    [
        {"type": "array", "maxItems": 500, "items": {"type": "string", "format": "binary"}},
        {
            "type": "object",
            "required": ["files"],
            "properties": {
                "files": {"type": "array", "maxItems": 500, "items": {"type": "string", "format": "binary"}}
            },
        },
        {
            "type": "object",
            "required": ["files"],
            "properties": {
                "files": {
                    "oneOf": [
                        {
                            "type": "array",
                            "maxItems": 500,
                            "items": {"type": "string", "format": "binary"},
                        }
                    ]
                }
            },
        },
    ],
    ids=["root", "property", "property-oneOf"],
)
def test_binary_array_near_max_items_does_not_break_coverage(ctx, app_runner, body_schema):
    raw_schema = build_schema(ctx, body=body_schema)
    app = ctx.openapi.make_permissive_flask_app(raw_schema)
    schema = ctx.openapi.from_full_schema(raw_schema)
    schema.config.update(base_url=app_runner.openapi_url(app, path=""))
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage"])
    cases = []

    with ctx.restore_hooks():

        @schemathesis.hook
        def before_call(context, case, **kwargs):
            cases.append(case)

        errors = [
            event.value
            for event in schemathesis.engine.from_schema(schema).execute()
            if isinstance(event, schemathesis.engine.events.NonFatalError)
        ]
    assert errors == []
    assert cases


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
        {
            "type": "array",
            "items": {"type": "null"},
            "contains": {"type": "null"},
            "minContains": 0,
            "maxContains": 0,
        },
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
        "zero-max-contains",
    ],
)
def test_positive_arrays_honor_contains(ctx, body):
    # A positive array must keep its `contains` match count within `minContains`/`maxContains`.
    collect_coverage_cases(ctx, body, positive=True, version="3.1.0")


@pytest.mark.parametrize(
    "body",
    [
        {
            "allOf": [
                {"type": "array", "items": {"type": "integer", "minimum": 5}},
                {"type": "array", "prefixItems": [{"type": "integer"}], "minItems": 1},
            ]
        },
        {
            "allOf": [
                {"type": "array", "prefixItems": [{"type": "integer"}], "minItems": 1},
                {"type": "array", "items": {"type": "integer", "minimum": 5}},
            ]
        },
        {
            "type": "array",
            "items": {"type": "integer", "minimum": 5},
            "anyOf": [{"prefixItems": [{"type": "integer"}], "minItems": 1}, {"maxItems": 0}],
        },
    ],
    ids=["items-first", "prefix-first", "any-of"],
)
def test_positive_arrays_keep_sibling_items_beside_prefix_items(ctx, body):
    # An `items` branch still judges the positions a sibling branch's `prefixItems` names.
    assert collect_coverage_cases(ctx, body, positive=True, version="3.1.0")


@pytest.mark.parametrize(
    "body",
    [
        {
            "allOf": [
                {"type": "array", "contains": {"type": "integer"}},
                {"type": "array", "contains": {"type": "string"}},
            ]
        },
        {
            "type": "array",
            "contains": {"type": "integer"},
            "allOf": [{"contains": {"type": "string"}}],
        },
    ],
    ids=["two-branches", "outer-and-branch"],
)
def test_positive_arrays_cover_disjoint_contains_branches(ctx, body):
    # Two `contains` each want their own matching item, not one item matching both.
    operation = body_operation(ctx, body, version="3.1.0")
    validator = jsonschema_rs.Draft202012Validator(body)

    bodies = [case.body for case in iter_cases(operation, GenerationMode.POSITIVE) if case.body is not NOT_SET]

    assert bodies, "No positive bodies generated"
    assert [value for value in bodies if not validator.is_valid(value)] == []


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


@pytest.mark.parametrize(
    ("version", "status"),
    [
        ("3.1.0", {"type": ["string", "null"], "enum": ["active", "archived"]}),
        ("3.0.2", {"type": "string", "enum": ["active", "archived"], "nullable": True}),
        ("2.0", {"type": "string", "enum": ["active", "archived"], "x-nullable": True}),
    ],
    ids=["type-array", "nullable", "x-nullable"],
)
def test_positive_nullable_enum_omits_null(ctx, version, status):
    # A nullable type paired with an `enum` that lacks null still forbids null.
    collect_coverage_cases(
        ctx,
        {"type": "object", "properties": {"status": status}, "required": ["status"]},
        positive=True,
        version=version,
    )


def test_coverage_negative_nullable_property_does_not_use_null_for_incorrect_type(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["note"],
            "properties": {"note": {"type": "string", "nullable": True}},
        },
        path="/items",
    )

    cases = scenario_cases(iter_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.INCORRECT_TYPE)
    mutated_values = [case.body["note"] for case in cases if isinstance(case.body, dict) and "note" in case.body]

    assert mutated_values, "Expected incorrect_type cases for the nullable property"
    assert None not in mutated_values
    assert any(not isinstance(value, str) for value in mutated_values)
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, cases=cases)


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
            # Missing required body
            {},
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
            # Missing required body
            {},
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
            # Missing required body
            {},
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


@pytest.mark.parametrize(
    "modes",
    [[GenerationMode.NEGATIVE], [GenerationMode.POSITIVE, GenerationMode.NEGATIVE]],
    ids=["negative", "mixed"],
)
def test_query_parameters_always_negative(modes):
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

    assert_coverage(schema, modes, ANY, ("/password", "get"))


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
    # Nothing about a bare string can be negated, so each required header is only tested by its own omission.
    assert_negative_coverage(
        schema,
        [
            {"headers": {"X-API-Key-1": ""}},
            {"headers": {"X-API-Key-2": ""}},
        ],
    )


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
                "headers": {"X-API-Key-1": "", "x-schemathesis-unknown-property": "42"},
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
                    "query": "",
                    "x-schemathesis-unknown-property": "42",
                },
            },
            {},
            {"query": {"query": ["", ""]}},
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


@pytest.mark.parametrize("location", ["header", "cookie"])
def test_incorrect_type_is_not_spelled_as_enum_value_on_the_wire(ctx, location):
    # Booleans, nulls and arrays travel as `true`, `null` and `null,null`, which these enums accept.
    schema = build_schema(
        ctx,
        [
            {
                "name": "X-Flag",
                "in": location,
                "required": True,
                "schema": {"type": "string", "enum": ["true", "false", "null", "null,null"]},
            },
        ],
    )
    container = "headers" if location == "header" else "cookies"
    assert_negative_coverage(
        schema,
        [
            {},
            {container: {"X-Flag": "AAA"}},
            {container: {"X-Flag": "{}"}},
            {container: {"X-Flag": "0.5"}},
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
                # Missing required body
                {},
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
                # Missing required body
                {},
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
                # Missing required body
                {},
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


@pytest.mark.parametrize(
    ("fmt", "min_length", "max_length"),
    [("email", 200, 254), ("uri", 100, 2083), ("date-time", 26, 30)],
)
def test_format_value_built_for_a_length_floor_above_it(ctx, fmt, min_length, max_length):
    # Without one, every length the window admits is out of reach and the operation gets no
    # positive case at all.
    operation = load_schema(
        ctx,
        [
            {
                "in": "query",
                "name": "value",
                "schema": {"type": "string", "format": fmt, "minLength": min_length, "maxLength": max_length},
                "required": True,
            },
        ],
    )["/foo"]["post"]
    validator = jsonschema_rs.Draft202012Validator({"type": "string", "format": fmt}, validate_formats=True)
    seen = []

    def test(case):
        seen.append(case.query["value"])

    run_positive_test(operation, test)

    assert seen
    for value in seen:
        assert min_length <= len(value) <= max_length, value
        assert validator.is_valid(value), value


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
    # When there are no constraints, then we can't generate negative values as everything will match the previous
    # schema, only omitting or duplicating the parameter
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
    assert_negative_coverage(schema, [{}, {"query": {"q": ["null", "null"]}}])


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
                "http://127.0.0.1/foo",
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
        if case.meta.phase.data.scenario in REQUEST_SHAPE_PROBES:
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


def test_optional_null_query_parameter_is_omitted(ctx):
    # No mainstream framework reads `?limit=null` as a JSON null; absence is how a query string says "no value".
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "query",
                "name": "limit",
                "required": False,
                "schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            }
        ],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, GenerationMode.POSITIVE)] == [{"limit": "0"}, {}]


def test_required_null_query_parameter_is_sent(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "query",
                "name": "limit",
                "required": True,
                "schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            }
        ],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, GenerationMode.POSITIVE)] == [
        {"limit": "0"},
        {"limit": "null"},
    ]


def test_negative_null_query_parameter_is_sent(ctx):
    operation = load_schema(
        ctx,
        parameters=[{"in": "query", "name": "limit", "required": False, "schema": {"type": "integer"}}],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, GenerationMode.NEGATIVE)] == [
        {"limit": "true"},
        {"limit": "null"},
        {"limit": "AAA"},
        {"limit": ["null", "null"]},
    ]


def test_null_inside_query_array_is_sent(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "query",
                "name": "ids",
                "required": False,
                "schema": {"type": "array", "items": {"type": "null"}, "minItems": 1, "maxItems": 1},
            }
        ],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, GenerationMode.POSITIVE)] == [{"ids": ["null"]}]


def test_optional_null_header_is_sent(ctx):
    # A header carries no "absent value" convention worth guessing at; only the query string gets the omission.
    operation = load_schema(
        ctx,
        parameters=[{"in": "header", "name": "X-Limit", "required": False, "schema": {"type": "null"}}],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert [case.headers for case in iter_cases(operation, GenerationMode.POSITIVE)] == [{"X-Limit": "null"}]


@pytest.mark.parametrize("location", ["header", "cookie", "query"])
def test_json_content_parameter_keeps_nested_types(ctx, location):
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "boolean"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    operation = load_schema(
        ctx,
        parameters=[
            {"in": location, "name": "X-F", "required": True, "content": {"application/json": {"schema": schema}}}
        ],
        method="get",
    )["/foo"]["GET"]
    container = LOCATION_TO_CONTAINER[location]
    assert [getattr(case, container) for case in iter_cases(operation, GenerationMode.POSITIVE)] == [
        {"X-F": '{"a": 0, "b": false}'},
        {"X-F": '{"a": 0, "b": true}'},
    ]
    negatives = [
        json.loads(getattr(case, container)["X-F"])
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
        if getattr(case, container)
    ]
    assert negatives == [
        False,
        None,
        "AAA",
        [None, None],
        {"a": AnyNumber(), "b": False},
        {"a": False, "b": False},
        {"a": None, "b": False},
        {"a": "AAA", "b": False},
        {"a": [None, None], "b": False},
        {"a": {}, "b": False},
        {"a": 0, "b": 0},
        {"a": 0, "b": None},
        {"a": 0, "b": "AAA"},
        {"a": 0, "b": [None, None]},
        {"a": 0, "b": {}},
        {"b": False},
        {"a": 0},
        {"a": 0, "b": False, "x-schemathesis-unknown-property": 42},
    ]


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


def test_content_type_probes(ctx):
    bodyless_operation = load_schema(ctx, path="/bodyless", method="get")["/bodyless"]["get"]
    assert [
        case.headers
        for case in scenario_cases(
            collect_cases(bodyless_operation, GenerationMode.NEGATIVE), CoverageScenario.MALFORMED_CONTENT_TYPE
        )
    ] == [{"Content-Type": "multipart/form-data"}]

    operation = body_operation(ctx, {"type": "object"})
    malformed_cases = scenario_cases(
        collect_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.MALFORMED_CONTENT_TYPE
    )
    unsupported_cases = scenario_cases(
        collect_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.UNSUPPORTED_CONTENT_TYPE
    )

    assert [case.headers for case in malformed_cases] == [{"Content-Type": "multipart/form-data"}]
    assert [case.headers for case in unsupported_cases] == [{"Content-Type": "text/plain"}]
    assert all(case.body is not NOT_SET and case.media_type == "application/json" for case in unsupported_cases)


def test_unsupported_content_type_uses_xml_when_text_is_declared(ctx):
    operation = body_operation(ctx, {"type": "object"}, media_type="text/plain")

    assert [
        case.headers
        for case in scenario_cases(
            collect_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.UNSUPPORTED_CONTENT_TYPE
        )
    ] == [{"Content-Type": "application/xml"}]


def test_malformed_multipart_probe_does_not_serialize_a_boundary(ctx):
    operation = body_operation(ctx, {"type": "object"}, media_type="multipart/form-data")
    (case,) = scenario_cases(collect_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.MALFORMED_CONTENT_TYPE)

    assert case.body is NOT_SET
    assert prepare_request(case, headers=None, config=SanitizationConfig(enabled=False)).headers["Content-Type"] == (
        "multipart/form-data"
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_content_type_probe_reports_server_error(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/items")
    def items():
        content_type = request.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data") and "boundary=" not in content_type:
            return "", 500
        return "", 200

    assert cli.run_openapi_app(app, "--phases=coverage", "--mode=negative", "--max-examples=1") == snapshot_cli


def test_content_type_probe_does_not_hide_plain_server_error(ctx, cli):
    # The probe's odd header must not become the only reproducer of a server error every request triggers.
    app, _ = ctx.openapi.make_flask_app({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/items")
    def items():
        return "", 500

    result = cli.run_openapi_app(
        app, "--phases=coverage,fuzzing", "--max-examples=1", "--checks=not_a_server_error", "--mode=all"
    )

    findings = dict(re.findall(r"\n- (Server error[^\n]*)\n.*?\n\s+(curl [^\n]+)", result.stdout, re.DOTALL))
    assert list(findings) == ["Server error"], result.stdout
    assert "Content-Type" not in findings["Server error"]


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (
            CoverageScenario.MALFORMED_CONTENT_TYPE,
            "`Content-Type: multipart/form-data` without a boundary returned 500, "
            "expected 400 Bad Request or 415 Unsupported Media Type\n\n"
            "Reject a malformed `Content-Type` before parsing the request body",
        ),
        (
            CoverageScenario.UNSUPPORTED_CONTENT_TYPE,
            "Undeclared `Content-Type: text/plain` returned 500, expected 415 Unsupported Media Type\n\n"
            "Reject media types the operation does not accept with 415",
        ),
    ],
    ids=["malformed", "unsupported"],
)
def test_content_type_probe_server_error_via_wsgi(ctx, scenario, message):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items", methods=["POST"])
    def items():
        if request.content_type != "application/json":
            return "", 500
        return "", 200

    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/items"]["POST"]
    (case,) = scenario_cases(collect_cases(operation, GenerationMode.NEGATIVE), scenario)

    with pytest.raises(FailureGroup) as exc_info:
        case.call_and_validate(checks=[not_a_server_error])

    assert [(type(failure), failure.message) for failure in exc_info.value.exceptions] == [
        (ContentTypeServerError, message)
    ]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_content_type_probe_ignores_non_server_errors(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/items")
    def items():
        return "", 200

    assert cli.run_openapi_app(app, "--phases=coverage", "--mode=negative", "--max-examples=1") == snapshot_cli


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


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], {"GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "TRACE", "QUERY"}),
        (["--exclude-method=TRACE"], {"GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "QUERY"}),
        (["--exclude-method-regex=^TRA"], {"GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "QUERY"}),
        (["--exclude-name=TRACE /items"], {"GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "QUERY"}),
        (["--include-method=GET"], {"GET", "PUT", "DELETE", "OPTIONS", "PATCH", "TRACE", "QUERY"}),
    ],
    ids=["no-filters", "exclude-method", "exclude-method-regex", "exclude-name", "include-method"],
)
def test_unexpected_methods_respect_exclusion_filters(ctx, cli, flags, expected):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        }
    )

    @app.route("/items", methods=["GET", "POST"])
    def items():
        return "", 200

    cli.run_openapi_app(app, "--phases=coverage", "--max-examples=5", *flags)

    assert {request.method for request in app.config["captured_requests"]} == expected


def test_unexpected_methods_respect_disabled_operations(ctx, cli):
    # Turning an operation off must also keep its method out of the unexpected-method probes.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            },
            "/admin": {"delete": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/items", methods=["GET", "POST"])
    def items():
        return "", 200

    @app.route("/admin", methods=["DELETE"])
    def admin():
        return "", 200

    cli.run_openapi_app(
        app,
        "--phases=coverage",
        "--max-examples=5",
        config={"operations": [{"include-method-regex": "DELETE", "enabled": False}]},
    )

    assert {request.method for request in app.config["captured_requests"]} == {
        "GET",
        "POST",
        "PUT",
        "OPTIONS",
        "PATCH",
        "TRACE",
        "QUERY",
    }


@pytest.mark.parametrize(
    ("uid_type", "rule", "probed"),
    [
        ("string", "/items/<uid>", {"DELETE", "OPTIONS", "POST", "PUT", "TRACE", "QUERY"}),
        ("integer", "/items/<int:uid>", {"DELETE", "OPTIONS", "PATCH", "POST", "PUT", "TRACE", "QUERY"}),
    ],
    ids=["segment-fits-parameter", "segment-violates-parameter"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unexpected_methods_skip_methods_declared_by_templated_sibling(ctx, cli, snapshot_cli, uid_type, rule, probed):
    item = {
        "get": {"responses": {"200": {"description": "OK"}}},
        "patch": {"responses": {"200": {"description": "OK"}}},
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items/headers": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/items/{uid}": {
                "parameters": [{"name": "uid", "in": "path", "required": True, "schema": {"type": uid_type}}],
                **item,
            },
        }
    )

    @app.route("/items/headers", methods=["GET"])
    def headers():
        return "", 200

    @app.route(rule, methods=["GET", "PATCH"])
    def item_by_uid(uid):
        return "", 404

    assert (
        cli.run_openapi_app(app, "--phases=coverage", "--checks=unsupported_method", "--max-examples=5") == snapshot_cli
    )
    assert {request.method for request in app.config["captured_requests"] if request.path == "/items/headers"} == {
        "GET",
        *probed,
    }


@pytest.mark.parametrize(
    ("sub_type", "rule", "probed"),
    [
        ("string", "/items/<id>/<sub>", {"DELETE", "OPTIONS", "POST", "PUT", "TRACE", "QUERY"}),
        ("integer", "/items/<id>/<int:sub>", {"DELETE", "OPTIONS", "PATCH", "POST", "PUT", "TRACE", "QUERY"}),
    ],
    ids=["segment-fits-parameter", "segment-violates-parameter"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unexpected_methods_skip_methods_declared_by_partly_templated_sibling(
    ctx, cli, snapshot_cli, sub_type, rule, probed
):
    id_parameter = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
    sub_parameter = {"name": "sub", "in": "path", "required": True, "schema": {"type": sub_type}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items/{id}/headers": {
                "parameters": [id_parameter],
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/items/{id}/{sub}": {
                "parameters": [id_parameter, sub_parameter],
                "get": {"responses": {"200": {"description": "OK"}}},
                "patch": {"responses": {"200": {"description": "OK"}}},
            },
        }
    )

    @app.route("/items/<id>/headers", methods=["GET"])
    def headers(id):
        return "", 200

    @app.route(rule, methods=["GET", "PATCH"])
    def item_part(id, sub):
        return "", 404

    assert (
        cli.run_openapi_app(app, "--phases=coverage", "--checks=unsupported_method", "--max-examples=5") == snapshot_cli
    )
    assert {request.method for request in app.config["captured_requests"] if request.path.endswith("/headers")} == {
        "GET",
        *probed,
    }


def test_unexpected_methods_ignore_invalid_operation_on_templated_sibling(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items/headers": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/items/{uid}": {"patch": None},
        }
    )
    operation = schema["/items/headers"]["GET"]
    schema.config.phases.coverage.unexpected_methods = {"patch"}
    assert [
        case.method
        for case in schema.iter_coverage_cases(
            operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generation_config=schema.config.generation,
        )
        if case.meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD
    ] == ["PATCH"]


def test_unexpected_methods_skip_sibling_parameter_missing_from_template(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items/headers": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/items/{uid}": {
                "patch": {
                    "parameters": [{"name": "other", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    operation = schema["/items/headers"]["GET"]
    schema.config.phases.coverage.unexpected_methods = {"patch"}
    assert [
        case.method
        for case in schema.iter_coverage_cases(
            operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generation_config=schema.config.generation,
        )
        if case.meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD
    ] == []


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


@pytest.mark.parametrize(
    ("declared", "security_scheme"),
    [
        ("x-vtex-api-appkey", {"type": "apiKey", "in": "header", "name": "X-VTEX-API-AppKey"}),
        ("authorization", {"type": "http", "scheme": "oauth"}),
    ],
    ids=["api-key", "http-auth"],
)
def test_security_scheme_does_not_shadow_declared_header(ctx, declared, security_scheme):
    # Header names are case-insensitive, so a credential spelled differently would replace the generated value.
    schema = load_schema(
        ctx,
        [{"name": declared, "in": "header", "required": True, "schema": {"type": "string", "minLength": 5}}],
        components={"securitySchemes": {"scheme": security_scheme}},
        security=[{"scheme": []}],
    )
    cases = collect_cases(schema["/foo"]["post"], GenerationMode.POSITIVE)
    assert [dict(case.headers) for case in cases] == [
        case.meta.raw_containers[ParameterLocation.HEADER] for case in cases
    ]


def test_path_item_header_does_not_shadow_operation_header(ctx):
    # Header names are case-insensitive, so a path-level spelling would replace the operation's generated value.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "parameters": [{"name": "X-Amz-Content-Sha256", "in": "header", "schema": {"type": "string"}}],
                "post": {
                    "parameters": [
                        {
                            "name": "x-amz-content-sha256",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 5},
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                },
            }
        }
    )
    cases = collect_cases(schema["/foo"]["post"], GenerationMode.POSITIVE)
    assert [dict(case.headers) for case in cases] == [
        case.meta.raw_containers[ParameterLocation.HEADER] for case in cases
    ]


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
        # No `Content-Type` means no body at all, unlike an empty payload the schema still allows.
        if not request.content_type:
            return jsonify({"message": "File is required"}), 400
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


WRAPPER = {"type": "object", "properties": {"location": {"type": "string"}, "sku": {"type": "string"}}}


@pytest.mark.parametrize(
    ("version", "container"),
    [("2.0", {"definitions": {"Wrapper": WRAPPER}}), ("3.0.2", {"components": {"schemas": {"Wrapper": WRAPPER}}})],
    ids=["swagger-2", "openapi-3.0"],
)
@pytest.mark.parametrize(
    "sibling", [{"required": ["location"]}, {"minProperties": 5}], ids=["required", "min-properties"]
)
def test_negative_bodies_ignore_keywords_next_to_root_ref(ctx, version, container, sibling):
    prefix = "#/definitions" if version == "2.0" else "#/components/schemas"
    body = {"$ref": f"{prefix}/Wrapper", **sibling}
    operation = body_operation(ctx, body, version=version, **container)
    # Draft 4 ignores keywords next to `$ref`, so mutating them yields bodies the spec still accepts.
    validator = jsonschema_rs.Draft4Validator({**body, **container})
    bodies = [
        case.body
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.body is not NOT_SET and body_mode(case) == GenerationMode.NEGATIVE
    ]
    assert bodies
    assert [value for value in bodies if validator.is_valid(value)] == []


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
                    "properties": {"file": {"type": "string", "format": "binary"}},
                },
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
        and case.meta.phase.data.scenario != CoverageScenario.MISSING_PARAMETER
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


def test_positive_number_multiple_above_large_minimum(ctx):
    schema = {"type": "number", "multipleOf": 1.1, "minimum": 7e16}
    operation = body_operation(ctx, schema)
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


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


def coverage_phase_cases(ctx, app_runner, raw_schema, mode):
    app = ctx.openapi.make_permissive_flask_app(raw_schema)
    schema = schemathesis.openapi.from_dict(raw_schema)
    schema.config.update(base_url=app_runner.openapi_url(app, path=""))
    schema.config.phases.update(phases=["coverage"])
    schema.config.generation.update(modes=[mode])
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    cases = []
    with ctx.restore_hooks():

        @schemathesis.hook
        def before_call(context, case, **kwargs):
            cases.append(case)

        for _ in schemathesis.engine.from_schema(schema).execute():
            pass
    return cases


def described_bodies(cases):
    return [
        (case.meta.phase.data.description, case.body)
        for case in cases
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
        and case.meta.phase.data.scenario != CoverageScenario.MISSING_PARAMETER
    ]


def test_additional_property_values_shared_by_two_types_emitted_once(ctx, app_runner):
    raw_schema = build_schema(ctx, body={"type": "object", "additionalProperties": {"type": ["integer", "number"]}})
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.POSITIVE)
    assert described_bodies(cases) == [
        ("Object with additional property: Valid number", {"x-schemathesis-additional": 0}),
        ("Valid object", {}),
    ]


def test_negative_body_for_any_of_with_only_a_false_branch(ctx, app_runner):
    raw_schema = build_schema(ctx, body={"anyOf": [False]}, version="3.1.0")
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert described_bodies(cases) == [
        ("Value is not allowed", {}),
        ("Value is not allowed", [None, None]),
        ("Value is not allowed", 0),
        ("Value is not allowed", ""),
        ("Value is not allowed", False),
        ("Value is not allowed", True),
        ("Value is not allowed", None),
    ]


def test_negative_bodies_when_a_required_property_admits_nothing(ctx, app_runner):
    raw_schema = build_schema(
        ctx,
        body={"type": "object", "required": ["a"], "properties": {"a": {"not": {}}, "b": {"type": "string"}}},
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert described_bodies(cases) == [
        ("b: Incorrect type", {"b": {}}),
        ("b: Incorrect type", {"b": [None, None]}),
        ("b: Incorrect type", {"b": None}),
        ("b: Incorrect type", {"b": False}),
        ("b: Incorrect type", {"b": 0}),
        ("a: Value is not allowed", {"b": "", "a": {}}),
        ("a: Value is not allowed", {"b": "", "a": [None, None]}),
        ("a: Value is not allowed", {"b": "", "a": 0}),
        ("a: Value is not allowed", {"b": "", "a": ""}),
        ("a: Value is not allowed", {"b": "", "a": False}),
        ("a: Value is not allowed", {"b": "", "a": True}),
        ("a: Value is not allowed", {"b": "", "a": None}),
        ("Missing required property: a", {"b": ""}),
        ("Incorrect type", [None, None]),
        ("Incorrect type", "AAA"),
        ("Incorrect type", None),
        ("Incorrect type", False),
        ("Incorrect type", 0),
    ]


def test_no_item_negatives_when_items_admit_everything(ctx, app_runner):
    raw_schema = build_schema(ctx, body={"type": "array", "items": True, "maxItems": 1}, version="3.1.0")
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert described_bodies(cases) == [
        ("Array with more items than allowed by maxItems", [None, None]),
        ("Incorrect type", {}),
        ("Incorrect type", "AAA"),
        ("Incorrect type", None),
        ("Incorrect type", False),
        ("Incorrect type", 0),
    ]


def test_negative_additional_property_values_next_to_explicit_true(ctx, app_runner):
    raw_schema = build_schema(
        ctx,
        body={
            "type": "object",
            "additionalProperties": True,
            "properties": {"o": {"type": "object", "additionalProperties": {"type": "integer"}}},
        },
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert [description for description, _ in described_bodies(cases)] == [
        "o -> Object with invalid additional property: Incorrect type",
        "o -> Object with invalid additional property: Incorrect type",
        "o -> Object with invalid additional property: Incorrect type",
        "o -> Object with invalid additional property: Incorrect type",
        "o -> Object with invalid additional property: Incorrect type",
        "o -> Object with invalid additional property: Incorrect type",
        "o: Incorrect type",
        "o: Incorrect type",
        "o: Incorrect type",
        "o: Incorrect type",
        "o: Incorrect type",
        "Incorrect type",
        "Incorrect type",
        "Incorrect type",
        "Incorrect type",
        "Incorrect type",
    ]


def test_positive_multipart_value_for_binary_one_of_branch(ctx, app_runner):
    raw_schema = build_schema(
        ctx,
        body={
            "type": "object",
            "properties": {"f": {"oneOf": [{"type": "string", "format": "binary"}, {"type": "integer"}]}},
            "required": ["f"],
        },
        media_type="multipart/form-data",
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.POSITIVE)
    assert [(description, type(body["f"]).__name__) for description, body in described_bodies(cases)] == [
        ("Object with valid 'f' value: Valid string", "Binary"),
        ("Valid object", "int"),
    ]


def test_query_enum_intersection_with_binary_entry(ctx, app_runner):
    # YAML `!!binary` values load as bytes.
    raw_schema = build_schema(
        ctx,
        parameters=[
            {
                "name": "q",
                "in": "query",
                "required": True,
                "schema": {"allOf": [{"enum": [b"a", "x"]}, {"enum": ["x", "y"]}]},
            }
        ],
        method="get",
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.POSITIVE)
    assert [case.query for case in cases] == [{"q": "x"}]


def test_only_required_case_skipped_when_optional_parameter_is_unsatisfiable(ctx, app_runner):
    raw_schema = build_schema(
        ctx,
        parameters=[
            {"name": "a", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "b", "in": "query", "required": False, "schema": {"not": {}}},
            {"name": "X-H", "in": "header", "required": True, "schema": {"type": "string"}},
            {"name": "X-K", "in": "header", "required": False, "schema": {"type": "string"}},
        ],
        method="get",
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.POSITIVE)
    assert [(case.meta.phase.data.description, case.query, case.headers) for case in cases] == [
        ("Only required properties", {"a": ""}, {"X-H": ""}),
        ("Default positive test case", {"a": ""}, {"X-H": "", "X-K": ""}),
    ]


def test_query_binary_default_is_the_positive_value(ctx, app_runner):
    # YAML `!!binary` values load as bytes.
    raw_schema = build_schema(
        ctx,
        parameters=[{"name": "q", "in": "query", "required": True, "schema": {"type": "string", "default": b"zz"}}],
        method="get",
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.POSITIVE)
    assert [case.query for case in cases] == [{"q": b"zz"}]


def test_format_negative_with_binary_keyword_values(ctx, app_runner):
    raw_schema = build_schema(
        ctx,
        parameters=[
            {
                "name": "q",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "format": "date", "default": b"zz"},
            },
            {"name": "r", "in": "query", "required": True, "schema": {"type": "string", "format": "date"}},
        ],
        body={"type": "object", "required": ["a"], "properties": {"a": {"type": "string", "example": b"x"}}},
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert [
        (case.query, case.body) for case in cases if case.meta.phase.data.scenario == CoverageScenario.INVALID_FORMAT
    ] == [({"q": "0", "r": ""}, {"a": b"x"})]


def test_positive_string_example_not_repeated_as_near_boundary_length(ctx, app_runner):
    raw_schema = build_schema(ctx, body={"type": "string", "pattern": "^a+$", "maxLength": 3, "example": "aa"})
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.POSITIVE)
    assert described_bodies(cases) == [("Maximum length string", "aaa"), ("Example value", "aa")]


def test_negative_min_properties_on_string_body_not_repeated(ctx, app_runner):
    raw_schema = build_schema(ctx, body={"type": "string", "minProperties": 1})
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert described_bodies(cases) == [
        ("Incorrect type", {}),
        ("Incorrect type", [None, None]),
        ("Incorrect type", None),
        ("Incorrect type", False),
        ("Incorrect type", 0),
    ]


def test_no_short_header_value_when_pattern_needs_non_latin_characters(ctx, app_runner):
    raw_schema = build_schema(
        ctx,
        parameters=[
            {
                "name": "X-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "pattern": "^€+$", "minLength": 3},
            }
        ],
        method="get",
    )
    cases = coverage_phase_cases(ctx, app_runner, raw_schema, GenerationMode.NEGATIVE)
    assert [case.meta.phase.data.description for case in cases if case.meta.phase.data.parameter == "X-Key"] == [
        "Missing `X-Key` at header",
        "Value not matching the '^€+$' pattern",
        "Incorrect type",
        "Incorrect type",
        "Incorrect type",
        "Incorrect type",
        "Incorrect type",
    ]


@pytest.mark.parametrize("location", ["header", "cookie"])
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "string"}, "example": ["ok"], "default": ["Āx"]},
        {"type": "array", "items": {"type": "string"}, "examples": [["a\nb"]]},
        {"type": "array", "items": {"type": "string"}, "enum": [["Āx"], ["ok"]]},
        {"type": "object", "properties": {"k": {"type": "string"}}, "example": {"k": "ok"}, "default": {"k": "a\nb"}},
    ],
    ids=["array-default", "array-examples", "array-enum", "object-default"],
)
def test_positive_header_containers_skip_hints_the_wire_cannot_carry(ctx, location, schema):
    operation = load_schema(
        ctx, parameters=[{"name": "X-Key", "in": location, "required": True, "schema": schema}], method="get"
    )["/foo"]["GET"]
    run_positive_test(operation, assert_requests_call)


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


def test_no_positive_body_under_unsatisfiable_allof_chain(ctx):
    # Base forbids `first`/`second` and Wrapper forbids `baseField`, so the required keys can never be present;
    # coverage may only emit schema-invalid negatives - never a relaxed body labelled positive.
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
    validator = body_validator(operation)
    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_test(operation, collect)

    assert {(body_mode(case), validator.is_valid(case.body)) for case in cases} == {(GenerationMode.NEGATIVE, False)}


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


def test_path_parameter_enum_value_with_slash_is_covered(ctx):
    # An enum member containing "/" must still be tried, percent-encoded, not dropped.
    operation = load_schema(
        ctx,
        path="/metrics/{metricId}",
        method="get",
        parameters=[
            {
                "name": "metricId",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "enum": ["requests/count", "users/count"]},
            }
        ],
    )["/metrics/{metricId}"]["get"]

    path_values = {case.path_parameters.get("metricId") for case in collect_cases(operation, GenerationMode.POSITIVE)}

    assert "requests%2Fcount" in path_values, f"Unexpected values: {path_values}"


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


def test_positive_body_covers_nested_enum_under_an_unfoldable_all_of(ctx):
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Payload"},
        path="/x",
        components={
            "schemas": {
                "Base": {"type": "object", "properties": {"id": {"type": "string"}}},
                "Payload": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "properties": {
                        "conditions": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["Succeeded", "Failed"]},
                        }
                    },
                },
            }
        },
    )
    assert {
        entry
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if isinstance(case.body, dict)
        for entry in case.body.get("conditions", [])
    } == {"Succeeded", "Failed"}


def test_positive_body_drawn_whole_when_an_inherited_property_cannot_be_folded(ctx):
    # No single spelling carries both patterns, so `code` is drawn against them together.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Payload"},
        path="/x",
        components={
            "schemas": {
                "Base": {
                    "type": "object",
                    "properties": {"code": {"type": "string", "pattern": "^a", "minLength": 2, "maxLength": 2}},
                    "required": ["code"],
                },
                "Payload": {
                    "type": "object",
                    "maxProperties": 1,
                    "additionalProperties": {"type": "string", "pattern": "b$", "minLength": 2, "maxLength": 2},
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                },
            }
        },
    )

    assert [(case.body, case.meta.phase.data.scenario) for case in iter_cases(operation, GenerationMode.POSITIVE)] == [
        ({"code": "ab"}, CoverageScenario.DEFAULT_POSITIVE_TEST)
    ]


def test_no_positive_body_when_an_inherited_required_property_admits_nothing(ctx):
    # `code` has to be a string and an integer at once, so no object fits.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Payload"},
        path="/x",
        components={
            "schemas": {
                "Base": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                "Payload": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                },
            }
        },
    )

    assert iter_cases(operation, GenerationMode.POSITIVE) == []


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


def test_positive_body_descends_past_a_second_use_of_a_shared_base(ctx):
    # A base reused at two nesting levels must not stop the walk at a pointer it has nothing to do with.
    operation = body_operation(
        ctx,
        {"$ref": "#/components/schemas/Outer"},
        path="/x",
        components={
            "schemas": {
                "Base": {"type": "object", "properties": {"id": {"type": "string"}}},
                "Outer": {
                    "type": "object",
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "properties": {"inner": {"$ref": "#/components/schemas/Middle"}},
                },
                "Middle": {
                    "type": "object",
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "properties": {"leaf": {"$ref": "#/components/schemas/Leaf"}},
                },
                "Leaf": {
                    "type": "object",
                    "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                },
            }
        },
    )
    assert {
        tuple(case.body["inner"]["leaf"]["tags"])
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if isinstance(case.body, dict) and "tags" in case.body.get("inner", {}).get("leaf", {})
    } == {(), ("",)}


def test_positive_body_descends_past_a_third_use_of_a_shared_base(ctx):
    # A base carrying no `$ref` of its own cannot recur, so a third nesting level leaves the walk below it intact.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "allOf": [{"$ref": "#/components/schemas/Base"}],
            "properties": {
                "inner": {
                    "type": "object",
                    "allOf": [{"$ref": "#/components/schemas/Base"}],
                    "properties": {"pools": {"type": "array", "items": {"$ref": "#/components/schemas/Base"}}},
                }
            },
        },
        path="/x",
        components={"schemas": {"Base": {"type": "object", "properties": {"id": {"type": "string"}}}}},
    )
    assert {
        json.dumps(case.body["inner"]["pools"])
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if isinstance(case.body, dict) and "pools" in case.body.get("inner", {})
    } == {"[]", '[{"id": ""}]', "[{}]"}


def test_negative_only_mode_emits_required_and_optional_combinations(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {"name": name, "in": "query", "required": name == "q", "schema": {"type": "integer", "minimum": 1}}
            for name in ("q", "r", "s")
        ],
        path="/items",
        method="get",
    )["/items"]["get"]

    def combination_queries(*modes):
        # Combinations carry the required parameter and leave out at least one optional one.
        return [
            case.query
            for case in iter_cases(operation, *modes)
            if case.meta.generation.mode == GenerationMode.NEGATIVE
            and "q" in case.query
            and not {"r", "s"} <= case.query.keys()
        ]

    assert combination_queries(GenerationMode.NEGATIVE) == combination_queries(
        GenerationMode.POSITIVE, GenerationMode.NEGATIVE
    )


def test_negative_combinations_are_invalid_under_the_declared_parameter_schema(ctx):
    # An unanchored pattern admits any string, so a length-bounded rewrite of it must not leak into negatives.
    declared = {"type": "string", "pattern": "[a-z]*", "maxLength": 8}
    operation = load_schema(
        ctx,
        parameters=[
            {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}},
            {"name": "t", "in": "query", "required": False, "schema": declared},
            {"name": "u", "in": "query", "required": False, "schema": {"type": "integer"}},
        ],
        path="/items",
        method="get",
    )["/items"]["get"]
    validator = jsonschema_rs.validator_for(declared)

    assert [
        case.query
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.generation.mode == GenerationMode.NEGATIVE
        and case.meta.phase.data.parameter == "t"
        and validator.is_valid(case.query["t"])
    ] == []


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


def test_unsatisfiable_required_header_emits_no_positive_case(ctx):
    # An optional sibling or a request body must not revive the positive cases the unsatisfiable header rules out.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "X-Required",
                "in": "header",
                "required": True,
                "schema": {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
            },
            {"name": "X-Optional", "in": "header", "required": False, "schema": {"type": "string"}},
        ],
        body={"type": "object", "properties": {"a": {"type": "string"}}},
        path="/route",
        method="post",
        version="3.1.0",
    )["/route"]["post"]
    cases = iter_cases(operation, *GenerationMode)
    positive = [case for case in cases if case.meta.generation.mode == GenerationMode.POSITIVE]
    assert positive == [], [(case.headers, case.body) for case in positive]


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


BODY_WITH_REQUIRED_PROPERTY = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}


def _required_parameter_cases(ctx, location):
    operation = body_operation(
        ctx,
        BODY_WITH_REQUIRED_PROPERTY,
        parameters=[{"name": "X-Token", "in": location, "required": True, "schema": {"type": "string"}}],
    )
    return collect_cases(operation, GenerationMode.NEGATIVE)


def _component_mode(case, location):
    info = case.meta.components.get(location)
    return info.mode if info is not None else None


@pytest.mark.parametrize("location", ["header", "query", "cookie"])
def test_required_parameter_without_negative_value_kept_in_negative_cases(ctx, location):
    # A case that means to mutate the body must not also drop a required parameter it cannot mutate.
    parameter_location = ParameterLocation(location)
    assert {
        (
            tuple(sorted(getattr(case, parameter_location.container_name).items())),
            _component_mode(case, parameter_location),
        )
        for case in _required_parameter_cases(ctx, location)
        if case.meta.phase.data.parameter != "X-Token" and case.meta.phase.data.scenario not in CONTENT_TYPE_PROBES
    } == {((("X-Token", ""),), GenerationMode.POSITIVE)}


@pytest.mark.parametrize("location", ["header", "query", "cookie"])
def test_missing_required_parameter_case_omits_only_that_parameter(ctx, location):
    parameter_location = ParameterLocation(location)
    assert [
        (
            getattr(case, parameter_location.container_name),
            _component_mode(case, parameter_location),
            case.meta.generation.mode,
            case.meta.phase.data.scenario,
        )
        for case in _required_parameter_cases(ctx, location)
        if case.meta.phase.data.parameter == "X-Token"
    ] == [({}, GenerationMode.NEGATIVE, GenerationMode.NEGATIVE, CoverageScenario.MISSING_PARAMETER)]


def _missing_body_cases(operation):
    return [
        case
        for case in scenario_cases(iter_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.MISSING_PARAMETER)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]


def test_missing_required_body_case(ctx):
    operation = body_operation(ctx, BODY_WITH_REQUIRED_PROPERTY)

    assert [
        (case.body, case.media_type, case.meta.generation.mode, case.meta.phase.data.description)
        for case in _missing_body_cases(operation)
    ] == [(NOT_SET, None, GenerationMode.NEGATIVE, "Missing request body")]


def test_missing_required_body_case_sends_no_content_type(ctx):
    operation = body_operation(ctx, BODY_WITH_REQUIRED_PROPERTY)
    (case,) = _missing_body_cases(operation)

    prepared = prepare_request(case, headers=None, config=SanitizationConfig(enabled=False))

    assert prepared.body is None
    assert "Content-Type" not in prepared.headers


def test_missing_body_case_clears_the_content_type_negation_it_drops(ctx):
    # The body-less request never sends the mutated `Content-Type`, so its headers carry no negation.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "parameters": [
                        {
                            "name": "Content-Type",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string", "enum": ["application/zip"]},
                        },
                        {"name": "X-Token", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": BODY_WITH_REQUIRED_PROPERTY}},
                    },
                    "responses": DEFAULT_RESPONSES,
                }
            }
        }
    )

    assert [
        (dict(case.headers), _component_mode(case, ParameterLocation.HEADER))
        for case in _missing_body_cases(schema["/items"]["POST"])
    ] == [({"X-Token": ""}, GenerationMode.POSITIVE)]


def test_missing_required_body_case_accepts_unsupported_media_type(ctx, response_factory):
    # A body-less request carries no `Content-Type`, so 415 is a conformant rejection.
    operation = body_operation(ctx, BODY_WITH_REQUIRED_PROPERTY)
    (case,) = _missing_body_cases(operation)

    assert negative_data_rejection(check_context(), response_factory.requests(status_code=415), case) is None


def test_no_missing_body_case_for_optional_body(ctx):
    operation = body_operation(ctx, BODY_WITH_REQUIRED_PROPERTY, body_required=False)

    assert _missing_body_cases(operation) == []


UNRESOLVABLE_REQUIRED_BODY = {
    "required": True,
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}},
}


def test_only_missing_body_case_when_required_body_reference_is_unresolvable(ctx):
    operation = load_schema(ctx, request_body=UNRESOLVABLE_REQUIRED_BODY)["/foo"]["post"]

    assert [
        (case.body, case.media_type, case.meta.generation.mode, case.meta.phase.data.description)
        for case in iter_cases(operation, *GenerationMode)
    ] == [(NOT_SET, None, GenerationMode.NEGATIVE, "Missing request body")]


def test_no_positive_cases_when_required_body_reference_is_unresolvable(ctx):
    operation = load_schema(ctx, request_body=UNRESOLVABLE_REQUIRED_BODY)["/foo"]["post"]

    assert iter_cases(operation, GenerationMode.POSITIVE) == []


PARTIALLY_UNRESOLVABLE_REQUIRED_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        },
        "application/xml": {"schema": {"$ref": "#/components/schemas/Missing"}},
    },
}


def test_resolvable_media_type_is_covered_when_sibling_reference_is_unresolvable(ctx):
    operation = load_schema(
        ctx,
        request_body=PARTIALLY_UNRESOLVABLE_REQUIRED_BODY,
        parameters=[{"name": "tag", "in": "query", "required": True, "schema": {"type": "string"}}],
    )["/foo"]["post"]

    assert {
        (case.media_type, case.meta.phase.data.parameter_location)
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
    } == {
        (None, ParameterLocation.BODY),
        ("application/json", None),
        ("application/json", ParameterLocation.BODY),
        ("application/json", ParameterLocation.HEADER),
        ("application/json", ParameterLocation.QUERY),
    }


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

    assert generate_hooked_cases(operation, GenerationMode.POSITIVE), "Expected coverage cases before filtering"

    @loaded.hook
    def filter_case(context, case):
        return False  # reject everything

    assert generate_hooked_cases(operation, GenerationMode.POSITIVE) == [], (
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
    cases = generate_hooked_cases(operation, GenerationMode.POSITIVE)

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


# YAML 1.1 parsers read a bare `on:` as the boolean key `True`.
BOOLEAN_KEY_BODY_SCHEMA = {
    "type": "object",
    "properties": {
        True: {"type": "string"},
        "name": {"type": "string"},
    },
}


def boolean_key_operation(ctx, location, property_schema):
    object_schema = {"type": "object", "properties": {True: property_schema, "name": {"type": "string"}}}
    if location == "query":
        parameters = [{"in": "query", "name": "key", "required": True, "schema": object_schema}]
        return load_schema(ctx, parameters, path="/hooks")["/hooks"]["post"]
    return body_operation(ctx, object_schema, path="/hooks")


@pytest.mark.parametrize("location", ["query", "body"])
def test_coverage_boolean_property_key(ctx, location):
    operation = boolean_key_operation(ctx, location, {"type": "string", "enum": ["ALPHA"]})
    cases = iter_cases(operation, GenerationMode.POSITIVE)
    values = [case.query if location == "query" else case.body for case in cases]
    assert {"true": "ALPHA", "name": ""} in values


@pytest.mark.parametrize("location", ["query", "body"])
def test_coverage_boolean_property_key_malformed_keyword(ctx, location):
    operation = boolean_key_operation(ctx, location, {"type": "string", "minLength": "x"})
    with pytest.raises(InvalidSchema):
        iter_cases(operation, GenerationMode.POSITIVE)


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


def test_coverage_positive_body_only_long_enough_pattern_branch_satisfies_format(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "issued": {
                    "type": "string",
                    "format": "date",
                    "pattern": "^(2020-01-02|x)$",
                    "minLength": 2,
                    "maxLength": 10,
                }
            },
        },
        path="/reports",
    )
    assert assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=generate_cases) == [
        {"issued": "2020-01-02"},
        {},
    ]


def test_coverage_positive_body_only_long_enough_pattern_branch_violates_format(ctx):
    # No other match fits the length window, so the property goes instead of shipping a non-date.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "issued": {
                    "type": "string",
                    "format": "date",
                    "pattern": "^(20200102|x)$",
                    "minLength": 2,
                    "maxLength": 8,
                }
            },
        },
        path="/reports",
    )
    assert assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=generate_cases) == [{}]


def test_items_with_conflicting_object_type_gets_negative_coverage(ctx):
    # The body itself can never be POSITIVE, but `items`' own sub-schema should still see a valid draw.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string", "example": "removeUserTargets"}},
            },
        },
    )
    cases = collect_cases(operation, GenerationMode.NEGATIVE)

    with_valid_array = [
        c
        for c in cases
        if isinstance(c.body, list)
        and c.body
        and all(isinstance(item, dict) and item.get("kind") == "removeUserTargets" for item in c.body)
    ]
    assert with_valid_array, (
        f"Expected a NEGATIVE case with 'kind' inside an array body. Got bodies: {[c.body for c in cases]}"
    )


def test_items_with_conflicting_object_type_and_example_stays_negative(ctx):
    # An `example` describing the object must not leak through as the array `items` forces generation into.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "items": {"type": "string"},
            "example": {"kind": "removeUserTargets"},
        },
    )
    cases = collect_cases(operation, GenerationMode.NEGATIVE)

    def body_is_negative(case):
        component = case.meta.components.get(ParameterLocation.BODY)
        return component is not None and component.mode == GenerationMode.NEGATIVE

    no_op_mutations = [c for c in cases if body_is_negative(c) and c.body == {"kind": "removeUserTargets"}]
    assert not no_op_mutations, f"Body: {[c.body for c in no_op_mutations]}"


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


@pytest.mark.parametrize("location", ["query", "header", "path"])
def test_negative_enum_skips_mismatched_entries_read_as_valid(ctx, location):
    # Servers read `1` sent for a boolean parameter as `true`.
    schema = {"type": "boolean", "enum": ["0", "1", True, False]}
    path = "/items/{p}" if location == "path" else "/items"
    parameters = [{"in": location, "name": "p", "required": True, "schema": schema}]
    operation = load_schema(ctx, parameters=parameters, path=path, method="get")[path]["get"]

    assert [
        case.meta.raw_containers[ParameterLocation(location)]["p"]
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.description == "Enum value with type mismatching the declared 'type'"
    ] == []


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
        version="3.1.0",
    )

    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, cases=iter_cases(operation, GenerationMode.NEGATIVE))


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


@pytest.mark.parametrize(
    ("annotations", "expected", "scenario"),
    [
        ({}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"example": 42}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"example": {"b": "oops"}}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"example": {"a": "oops"}}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"default": "oops"}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"examples": ["nope", {"b": "oops"}]}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"example": 42, "examples": ["nope"], "default": 42}, [{"a": 0}], CoverageScenario.VALID_OBJECT),
        ({"example": {"a": 3}}, [{"a": 3}], CoverageScenario.EXAMPLE_VALUE),
        ({"default": {"a": 3}}, [{"a": 3}, {"a": 0}], CoverageScenario.DEFAULT_VALUE),
        ({"examples": ["nope", {"a": 3}, 42]}, [{"a": 3}], CoverageScenario.EXAMPLE_VALUE),
        ({"example": 42, "default": {"a": 3}}, [{"a": 3}, {"a": 0}], CoverageScenario.DEFAULT_VALUE),
        ({"example": {"a": 3}, "default": "oops"}, [{"a": 3}], CoverageScenario.EXAMPLE_VALUE),
    ],
)
def test_positive_object_annotations_fall_back_to_template(ctx, annotations, expected, scenario):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], **annotations}
    operation = body_operation(ctx, schema)
    operation.schema.config.generation.update(modes=[GenerationMode.POSITIVE])

    cases = list(iter_cases(operation, GenerationMode.POSITIVE))

    assert [case.body for case in cases] == expected
    assert cases[0].meta.phase.data.scenario == scenario
    validator = jsonschema_rs.Draft4Validator(schema)
    for case in cases:
        validator.validate(case.body)


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
        try:
            negative_data_rejection(ctx_check, response, case)
        except AcceptedNegativeData:
            pass


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
        if case.body is NOT_SET or isinstance(case.body, dict):
            continue
        assert negative_data_rejection(ctx_check, response, case) is None, (
            f"False positive: body {case.body!r} ({type(case.body).__name__})"
        )


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


@pytest.mark.parametrize(
    "property_schema",
    [
        {"$ref": "#/components/schemas/Item"},
        {"type": "object", "$ref": "#/components/schemas/Item"},
    ],
    ids=["plain-ref", "ref-with-sibling"],
)
def test_negative_coverage_emits_invalid_format_for_referenced_body_property(ctx, property_schema):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"a": property_schema},
                                }
                            }
                        },
                    },
                    "responses": DEFAULT_RESPONSES,
                }
            }
        },
        version="3.1.0",
        components={"schemas": {"Item": {"properties": {"url": {"format": "uri"}}}}},
    )
    operation = schema["/items"]["POST"]

    assert [
        (case.body, case.meta.phase.data.description)
        for case in scenario_cases(iter_cases(operation, GenerationMode.NEGATIVE), CoverageScenario.INVALID_FORMAT)
    ] == [
        ({"a": {"url": ""}}, "a -> url: Value not matching the 'uri' format"),
    ]


def test_negative_coverage_emits_invalid_format_for_duration_body_property(ctx):
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "required": ["retentionTime"],
            "properties": {"retentionTime": {"type": "string", "format": "duration"}},
        },
        path="/tasks",
        version="2.0",
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)

    assert [
        case.body["retentionTime"]
        for case in scenario_cases(cases, CoverageScenario.INVALID_FORMAT)
        if isinstance(case.body, dict) and "retentionTime" in case.body
    ], "no INVALID_FORMAT case emitted for body property with format: duration"


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
    schema.config.phases.coverage.unexpected_methods = unexpected_methods

    seen: list[tuple[str, str]] = []
    seen_dedup: set[tuple[str, str]] = set()
    for declared in ("GET", "POST", "PUT", "DELETE"):
        for case in schema.iter_coverage_cases(
            schema["/items"][declared],
            generation_modes=[GenerationMode.NEGATIVE],
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


def test_content_type_header_keeps_declared_value_when_body_media_type_conflicts(ctx):
    # A declared Content-Type header must keep a value its own schema admits, even when the body media type differs.
    operation = body_operation(
        ctx,
        {"type": "string", "format": "binary"},
        media_type="application/octet-stream",
        parameters=[
            {
                "in": "header",
                "name": "Content-type",
                "schema": {"type": "string", "default": "application/x-tar", "enum": ["application/x-tar"]},
            }
        ],
    )
    values = set()
    for case in collect_cases(operation, GenerationMode.POSITIVE):
        headers = case.meta.raw_containers.get(ParameterLocation.HEADER) or {}
        if "Content-type" in headers:
            values.add(headers["Content-type"])
    assert values == {"application/x-tar"}


def test_content_type_header_pins_to_a_declared_body_media_type_it_admits(ctx):
    # With several bodies declared, the pinned value names one of them rather than any string the header allows.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "in": "header",
                "name": "Content-Type",
                "schema": {"type": "string", "enum": ["text/plain", "application/xml"]},
            }
        ],
        request_body={
            "required": True,
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "application/xml": {"schema": {"type": "object"}},
            },
        },
    )["/foo"]["post"]
    values = set()
    for case in collect_cases(operation, GenerationMode.POSITIVE):
        if case.body is NOT_SET:
            continue
        headers = case.meta.raw_containers.get(ParameterLocation.HEADER) or {}
        values.add(headers.get("Content-Type"))
    assert values == {"application/xml"}


def test_content_type_header_matches_each_body_media_type(ctx):
    operation = load_schema(
        ctx,
        parameters=[{"in": "header", "name": "Content-Type", "schema": {"type": "string"}}],
        request_body={
            "required": True,
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "text/plain": {"schema": {"type": "string"}},
            },
        },
    )["/foo"]["post"]
    pairs = {
        (case.media_type, (case.headers or {}).get("Content-Type"))
        for case in collect_cases(operation, GenerationMode.POSITIVE)
        if case.body is not NOT_SET
    }
    assert pairs == {("application/json", "application/json"), ("text/plain", "text/plain")}


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


def test_coverage_parameter_negatives_survive_unserializable_body_media_type(ctx):
    # The declared media type has no serializer, but path-parameter negatives do not depend on the body.
    operation = load_schema(
        ctx,
        parameters=[
            {"in": "path", "name": "name", "required": True, "type": "string", "maxLength": 3},
            {"in": "body", "name": "content", "required": True, "schema": {"type": "string"}},
        ],
        path="/items/{name}",
        method="put",
        version="2.0",
        consumes=["text/powershell"],
    )["/items/{name}"]["PUT"]
    cases = generate_cases(operation, GenerationMode.NEGATIVE)

    assert [
        case.path_parameters["name"] for case in scenario_cases(cases, CoverageScenario.STRING_ABOVE_MAX_LENGTH)
    ] == ["0000"]


def test_negative_coverage_violates_maximum_wider_than_int64_range(ctx):
    # `maximum` above the `format: int64` ceiling still has to be exceeded, or the bound is never tested.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {
                "value": {"type": "integer", "format": "int64", "maximum": 9223372036854776000, "minimum": 0}
            },
            "required": ["value"],
        },
        path="/x",
        version="2.0",
    )
    cases = iter_cases(operation, GenerationMode.NEGATIVE)

    assert [case.body["value"] for case in scenario_cases(cases, CoverageScenario.VALUE_ABOVE_MAXIMUM)] == [
        9223372036854776001
    ]


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


def test_path_parameter_enum_never_blanks_the_path_segment(ctx):
    # A blank segment collapses the URL onto another operation, so the case tests something else.
    operation = load_schema(
        ctx,
        parameters=[
            {"name": "v", "in": "path", "required": True, "schema": {"type": "string", "enum": ["", "active"]}}
        ],
        path="/p/{v}",
    )["/p/{v}"]["post"]
    assert {case.path_parameters["v"] for case in collect_cases(operation, GenerationMode.POSITIVE)} == {"active"}


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
    # Non-dict values collapse to `name=` on the wire, so every type violation repeats one request.
    operation = load_schema(
        ctx,
        parameters=[{"in": "query", "name": "filter", "required": False, "schema": {"type": "object"}}],
        method="get",
    )["/foo"]["GET"]
    assert [case.query for case in iter_cases(operation, *GenerationMode)] == [{}, {"filter": ""}]


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
        {},
        {"filter": ""},
        {"sort_by": ""},
        {"x-schemathesis-unknown-property": "42"},
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


def test_body_examples_mismatching_schema_do_not_suppress_positive_generation(ctx):
    # A spec's `examples` can describe a shape unrelated to the body schema (real-world specs
    # often disagree); they shouldn't poison generation into never producing a valid body.
    operation = load_schema(
        ctx,
        parameters=[
            {"name": "feedType", "in": "query", "required": True, "schema": {"type": "string", "enum": ["a", "b"]}}
        ],
        request_body={
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                        "required": ["file"],
                    },
                    "examples": {
                        "json1": {"value": {"Price": [{"foo": "bar"}], "PriceHeader": {"a": 1}}},
                        "xml1": {"value": {"Price": [{"baz": "qux"}], "PriceHeader": {"b": 2}}},
                    },
                }
            },
        },
    )["/foo"]["post"]
    cases = iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
    scenarios = {c.meta.phase.data.scenario for c in cases}
    assert CoverageScenario.VALID_OBJECT in scenarios, scenarios
    assert CoverageScenario.INVALID_ENUM_VALUE in scenarios, scenarios


def test_object_parameter_example_out_of_range_as_float32_is_not_a_positive_value(ctx):
    # `0.99999999` narrows to `1.0` in single precision, which `exclusiveMaximum` rejects.
    operation = load_schema(
        ctx,
        parameters=[
            {
                "name": "q",
                "in": "query",
                "required": True,
                "style": "deepObject",
                "explode": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "f": {"type": "number", "format": "float", "maximum": 1, "exclusiveMaximum": True},
                    },
                    "required": ["f"],
                },
                "example": {"f": 0.99999999},
            }
        ],
    )["/foo"]["post"]
    values = [case.query["q[f]"] for case in iter_cases(operation, GenerationMode.POSITIVE)]
    assert "0.99999999" not in values, values


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


def test_custom_media_type_body_emits_no_positive_case_in_negative_mode(ctx):
    schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
    )["/foo"]["post"]
    assert [
        (case.meta.generation.mode, case.meta.phase.data.scenario)
        for case in iter_cases(operation, GenerationMode.NEGATIVE)
    ] == [(GenerationMode.NEGATIVE, CoverageScenario.MISSING_PARAMETER)]


@pytest.mark.parametrize("is_required", [True, False], ids=["required", "optional"])
def test_multipart_body_with_custom_encoding_yields_positive_case(ctx, is_required):
    schemathesis.openapi.media_type("image/png", st.just(b"\x89PNG"))
    operation = load_schema(
        ctx,
        request_body={
            "required": is_required,
            "content": {
                "multipart/form-data": {
                    "schema": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}},
                    "encoding": {"file": {"contentType": "image/png"}},
                }
            },
        },
    )["/foo"]["post"]
    assert [
        (case.meta.generation.mode, case.body)
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if case.body is not NOT_SET
    ] == [(GenerationMode.POSITIVE, {"file": b"\x89PNG"})]


def test_combination_cases_deduplicate_repeated_requests(ctx):
    # 'X-Token' is the same header as 'x-token', so the all-headers combination repeats the default request.
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
        ("default_positive_test", "positive", None, {"x-token": "secret", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "0", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "0.5", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "true", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "null", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "null,null", "X-Other": "other"}),
        ("incorrect_type", "negative", "x-token", {"x-token": "{}", "X-Other": "other"}),
        ("invalid_enum_value", "negative", "x-token", {"x-token": "AAA", "X-Other": "other"}),
        ("incorrect_type", "negative", "X-Other", {"x-token": "secret", "X-Other": "0"}),
        ("incorrect_type", "negative", "X-Other", {"x-token": "secret", "X-Other": "0.5"}),
        ("incorrect_type", "negative", "X-Other", {"x-token": "secret", "X-Other": "true"}),
        ("incorrect_type", "negative", "X-Other", {"x-token": "secret", "X-Other": "null"}),
        ("incorrect_type", "negative", "X-Other", {"x-token": "secret", "X-Other": "null,null"}),
        ("incorrect_type", "negative", "X-Other", {"x-token": "secret", "X-Other": "{}"}),
        ("invalid_enum_value", "negative", "X-Other", {"x-token": "secret", "X-Other": "AAA"}),
        ("missing_parameter", "negative", "x-token", {"X-Other": "other"}),
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
    ]


@pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
@pytest.mark.parametrize("boolean_schema", [True, False], ids=["true", "false"])
def test_boolean_parameter_schema(ctx, location, boolean_schema):
    path = "/items/{p}" if location == "path" else "/items"
    operation = load_schema(
        ctx,
        parameters=[{"name": "p", "in": location, "required": True, "schema": boolean_schema}],
        path=path,
        method="get",
        version="3.1.0",
    )[path]["get"]
    cases = iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
    assert cases
    for case in cases:
        assert_requests_call(case)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("query", [2, 0.5, "null", "AAA", ["null", "null"]]),
        ("header", [2, 0.5, None, "AAA", [None, None], {}]),
        ("cookie", [2, 0.5, None, "AAA", [None, None], {}]),
    ],
)
def test_boolean_parameter_type_negatives_are_not_boolean_spellings(ctx, location, expected):
    # Servers read text such as `0` or `true` sent for a boolean parameter as a valid boolean.
    operation = load_schema(
        ctx,
        parameters=[{"name": "p", "in": location, "required": True, "schema": {"type": "boolean"}}],
        path="/items",
        method="get",
    )["/items"]["get"]
    assert [
        case.meta.raw_containers[ParameterLocation(location)]["p"]
        for case in iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
        if case.meta.phase.data.scenario == CoverageScenario.INCORRECT_TYPE
    ] == expected


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"anyOf": [{"type": "string"}, False]},
        {"oneOf": [{"type": "string"}, False]},
        {"allOf": [{"type": "string"}, True]},
    ],
    ids=["plain", "anyOf-false", "oneOf-false", "allOf-true"],
)
def test_string_query_parameter_with_boolean_subschema_has_no_wire_valid_negatives(ctx, schema):
    # Every query value is a string on the wire, so only omitting the parameter is negative.
    operation = load_schema(
        ctx,
        parameters=[{"name": "q", "in": "query", "required": True, "schema": schema}],
        path="/items",
        method="get",
        version="3.1.0",
    )["/items"]["get"]
    assert [(case.query, case.meta.phase.data.scenario) for case in iter_cases(operation, GenerationMode.NEGATIVE)] == [
        ({}, CoverageScenario.MISSING_PARAMETER)
    ]


@pytest.mark.parametrize(
    "media_type",
    [
        "application/json",
        "text/plain",
        "application/xml",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
    ],
)
@pytest.mark.parametrize("boolean_schema", [True, False], ids=["true", "false"])
def test_boolean_body_schema(ctx, media_type, boolean_schema):
    operation = body_operation(ctx, boolean_schema, media_type=media_type, version="3.1.0")
    cases = iter_cases(operation, GenerationMode.POSITIVE, GenerationMode.NEGATIVE)
    assert cases
    for case in cases:
        assert_requests_call(case)


ARRAY_QUERY_PARAMETER = {
    "in": "query",
    "name": "ids",
    "required": True,
    "schema": {"type": "array", "items": {"type": "string"}, "minItems": 0},
}
ARRAY_QUERY_PARAMETER_WITH_MIN_ITEMS = {
    "in": "query",
    "name": "ids",
    "required": True,
    "schema": {"type": "array", "items": {"type": "string"}, "minItems": 1},
}
STRING_QUERY_PARAMETER_DISALLOWING_EMPTY = {
    "in": "query",
    "name": "ids",
    "required": True,
    "allowEmptyValue": False,
    "schema": {"type": "string"},
}
STRING_QUERY_PARAMETER_WITH_MIN_LENGTH = {
    "in": "query",
    "name": "ids",
    "required": True,
    "allowEmptyValue": False,
    "schema": {"type": "string", "minLength": 1},
}
QUERY_PARAMETERS_ALLOWING_EMPTY = [
    {"in": "query", "name": "value", "required": True, "allowEmptyValue": True, "schema": schema}
    for schema in (
        {"type": "boolean"},
        {"type": "string", "enum": ["asc"]},
        {"type": "string", "minLength": 1},
    )
]


@pytest.mark.parametrize(
    ("parameter", "scenario", "empty_value", "violates_schema"),
    [
        (ARRAY_QUERY_PARAMETER, CoverageScenario.ARRAY_BELOW_MIN_ITEMS, [], False),
        (ARRAY_QUERY_PARAMETER_WITH_MIN_ITEMS, CoverageScenario.ARRAY_BELOW_MIN_ITEMS, [], True),
        (STRING_QUERY_PARAMETER_DISALLOWING_EMPTY, CoverageScenario.STRING_BELOW_MIN_LENGTH, "", False),
        (STRING_QUERY_PARAMETER_WITH_MIN_LENGTH, CoverageScenario.STRING_BELOW_MIN_LENGTH, "", True),
    ],
    ids=["array-without-min-items", "array-with-min-items", "string-without-min-length", "string-with-min-length"],
)
def test_empty_query_value_is_negative_only_when_the_schema_forbids_it(
    ctx, parameter, scenario, empty_value, violates_schema
):
    # An empty value serializes to nothing, so generation avoids it — but the published schema may still admit it.
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]

    matching = [
        case
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.scenario == scenario and case.query.get("ids") == empty_value
    ]

    assert bool(matching) is violates_schema, f"{empty_value!r} labelled negative: {not violates_schema}"


@pytest.mark.parametrize(
    "parameter",
    [
        {"in": "query", "name": "q", "required": True, "schema": {"type": "string", "nullable": True}},
        {"in": "query", "name": "q", "required": True, "allowEmptyValue": True, "schema": {"type": "string"}},
    ],
    ids=["nullable", "allow-empty-value"],
)
def test_string_query_parameter_beside_another_branch_has_no_negatives_valid_as_text(ctx, parameter):
    # Every text is a string or reads as null, so no value has an incorrect type.
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]

    assert [
        (case.meta.phase.data.scenario, case.query.get("q"))
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter == "q"
    ] == [(CoverageScenario.MISSING_PARAMETER, None)]


@pytest.mark.parametrize("location", ["header", "cookie"])
def test_nullable_string_parameter_has_no_negatives_valid_as_text(ctx, location):
    # A list of nulls travels as the text `null,null`, which is a valid string.
    parameter = {"in": location, "name": "q", "required": True, "schema": {"type": "string", "nullable": True}}
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]

    assert [
        case.meta.phase.data.scenario
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter == "q"
    ] == [CoverageScenario.MISSING_PARAMETER]


@pytest.mark.parametrize("location", ["header", "cookie"])
def test_string_parameter_with_items_has_no_array_negatives_valid_as_text(ctx, location):
    # Arrays of empty strings travel as empty text, which is a valid string.
    schema = {"type": "string", "items": {"type": "string"}}
    parameter = {"in": location, "name": "q", "required": True, "schema": schema}
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]

    assert [
        case.meta.raw_containers[ParameterLocation(location)]["q"]
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter == "q" and case.meta.phase.data.scenario == CoverageScenario.INCORRECT_TYPE
    ] == []


@pytest.mark.parametrize(
    ("schema", "allow_empty_value", "expected"),
    [
        ({"type": "integer", "nullable": True}, False, ["AAA", "true"]),
        ({"type": "boolean", "nullable": True}, False, ["AAA", "0.5"]),
        ({"type": "boolean"}, True, ["-58800", ["null", "null"], "AAA", "null", "0.5"]),
    ],
    ids=["nullable-integer", "nullable-boolean", "allow-empty-value-boolean"],
)
def test_query_parameter_beside_another_branch_has_no_negatives_read_as_valid(ctx, schema, allow_empty_value, expected):
    # Servers read `null` as null and `true` or `0` as booleans.
    parameter = {"in": "query", "name": "q", "required": True, "allowEmptyValue": allow_empty_value, "schema": schema}
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]

    assert [
        case.query.get("q")
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter == "q" and case.meta.phase.data.scenario != CoverageScenario.MISSING_PARAMETER
    ] == expected


@pytest.mark.parametrize(
    ("parameter", "reports_failure"),
    [(STRING_QUERY_PARAMETER_DISALLOWING_EMPTY, False), (STRING_QUERY_PARAMETER_WITH_MIN_LENGTH, True)],
    ids=["without-min-length", "with-min-length"],
)
def test_negative_data_rejection_for_empty_query_string(ctx, response_factory, parameter, reports_failure):
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]
    response = response_factory.requests(status_code=200)

    reported = []
    for case in collect_cases(operation, GenerationMode.NEGATIVE):
        if case.query.get("ids") != "":
            continue
        try:
            negative_data_rejection(check_context(), response, case)
        except AcceptedNegativeData as exc:
            reported.append(str(exc))

    assert bool(reported) is reports_failure, reported


@pytest.mark.parametrize("parameter", QUERY_PARAMETERS_ALLOWING_EMPTY, ids=["boolean", "enum", "min-length"])
def test_allow_empty_value_is_not_negative(ctx, parameter):
    operation = load_schema(ctx, parameters=[parameter], method="get")["/foo"]["GET"]

    assert all(case.query.get("value") != "" for case in collect_cases(operation, GenerationMode.NEGATIVE))


@pytest.mark.parametrize(
    ("parameter", "version", "path", "expected"),
    [
        (
            {"in": "query", "type": "string", "minimum": 1, "maximum": 15},
            "2.0",
            "/foo",
            [(CoverageScenario.MISSING_PARAMETER, None)],
        ),
        ({"in": "path", "schema": {"type": "string", "minimum": 1}}, "3.0.2", "/foo/{x}", []),
        (
            {"in": "header", "schema": {"type": "string", "exclusiveMinimum": 1, "maximum": 15}},
            "3.1.0",
            "/foo",
            [(CoverageScenario.MISSING_PARAMETER, None)],
        ),
    ],
    ids=["query-swagger", "path", "header-exclusive"],
)
def test_numeric_bounds_on_string_parameter_are_not_negated(ctx, parameter, version, path, expected):
    # Numeric bounds never constrain a string, so the text "0" or "16" is a valid value.
    operation = load_schema(
        ctx, parameters=[{**parameter, "name": "x", "required": True}], version=version, path=path, method="get"
    )[path]["GET"]
    container = LOCATION_TO_CONTAINER[ParameterLocation(parameter["in"])]

    assert [
        (case.meta.phase.data.scenario, getattr(case, container).get("x"))
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter == "x"
    ] == expected


SECOND_REQUIRED_QUERY_PARAMETER = {"in": "query", "name": "kind", "required": True, "schema": {"type": "string"}}
OPTIONAL_QUERY_PARAMETER = {"in": "query", "name": "extra", "schema": {"type": "string"}}
# An exploded empty array leaves the wire request identical to one that omits the parameter.
ARRAY_QUERY_PARAMETER_NOT_EXPLODED = {**ARRAY_QUERY_PARAMETER, "style": "form", "explode": False}
ARRAY_QUERY_PARAMETER_NOT_EXPLODED_WITH_MIN_ITEMS = {
    **ARRAY_QUERY_PARAMETER_WITH_MIN_ITEMS,
    "style": "form",
    "explode": False,
}


@pytest.mark.parametrize(
    ("parameter", "scenario", "empty_value", "violates_schema"),
    [
        (ARRAY_QUERY_PARAMETER_NOT_EXPLODED, CoverageScenario.ARRAY_BELOW_MIN_ITEMS, [], False),
        (ARRAY_QUERY_PARAMETER_NOT_EXPLODED_WITH_MIN_ITEMS, CoverageScenario.ARRAY_BELOW_MIN_ITEMS, [], True),
        (STRING_QUERY_PARAMETER_DISALLOWING_EMPTY, CoverageScenario.STRING_BELOW_MIN_LENGTH, "", False),
        (STRING_QUERY_PARAMETER_WITH_MIN_LENGTH, CoverageScenario.STRING_BELOW_MIN_LENGTH, "", True),
    ],
    ids=["array-without-min-items", "array-with-min-items", "string-without-min-length", "string-with-min-length"],
)
def test_empty_query_value_is_negative_only_when_the_schema_forbids_it_among_other_parameters(
    ctx, parameter, scenario, empty_value, violates_schema
):
    operation = load_schema(
        ctx,
        parameters=[parameter, SECOND_REQUIRED_QUERY_PARAMETER, OPTIONAL_QUERY_PARAMETER],
        method="get",
    )["/foo"]["GET"]

    matching = [
        case
        for case in collect_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.scenario == scenario
        and case.meta.raw_containers.get(ParameterLocation.QUERY, {}).get("ids") == empty_value
    ]

    assert bool(matching) is violates_schema, f"{empty_value!r} labelled negative: {not violates_schema}"


def test_positive_multipart_upload_sends_a_named_real_file(ctx):
    # Handlers that decode the upload need a parseable file with a matching extension to reach their own logic.
    operation = body_operation(
        ctx,
        {"type": "object", "properties": {"image": {"type": "string", "format": "binary"}}, "required": ["image"]},
        media_type="multipart/form-data",
        path="/upload",
    )
    files = [
        REQUESTS_TRANSPORT.serialize_case(case)["files"]
        for case in iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]
    assert [("image", ("image.png", ANY))] in files, files
    assert any(part[1][1].startswith(b"\x89PNG\r\n\x1a\n") for parts in files for part in parts), files


def test_positive_body_merges_ref_sibling_properties_into_the_target(ctx):
    body = {
        "oneOf": [
            {"$ref": "#/components/schemas/Base", "properties": {"extra": {"type": "integer"}}, "required": ["extra"]},
            {"type": "string"},
        ]
    }
    operation = body_operation(
        ctx,
        body,
        version="3.1.0",
        components={
            "schemas": {"Base": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}
        },
    )
    assert assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases) == [
        "",
        {"name": "", "extra": 0},
    ]


@pytest.mark.parametrize(
    ("version", "body", "target"),
    [
        (
            "3.1.0",
            {"type": "object", "properties": {"a": {"$ref": "#/components/schemas/A", "anyOf": [{"type": "null"}]}}},
            {"type": "boolean"},
        ),
        (
            "3.1.0",
            {
                "type": "object",
                "properties": {"a": {"allOf": [{"$ref": "#/components/schemas/A", "anyOf": [{"type": "null"}]}]}},
            },
            {"type": "boolean"},
        ),
        (
            "3.0.2",
            {"oneOf": [{"type": "null"}, {"$ref": "#/components/schemas/A", "anyOf": [{"type": "null"}]}]},
            {"type": "boolean"},
        ),
    ],
    ids=["property-ref-sibling", "property-ref-sibling-in-all-of", "one-of-ref-sibling-under-draft4"],
)
def test_positive_bodies_follow_ref_sibling_keywords_as_the_draft_reads_them(ctx, version, body, target):
    operation = body_operation(ctx, body, version=version, components={"schemas": {"A": target}})
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


@pytest.mark.parametrize(
    ("version", "body", "target"),
    [
        (
            "3.1.0",
            {
                "oneOf": [
                    {"type": "null"},
                    {"$ref": "#/components/schemas/A", "anyOf": [{"type": "null"}]},
                    {"type": "array", "items": {"type": "null"}},
                ]
            },
            {},
        ),
        (
            "3.0.2",
            {"oneOf": [{"type": "null"}, {"$ref": "#/components/schemas/A", "anyOf": [{"type": "null"}]}]},
            {"type": "boolean"},
        ),
    ],
    ids=["draft2020", "draft4"],
)
def test_negative_bodies_violate_one_of_with_ref_sibling_keywords(ctx, version, body, target):
    operation = body_operation(ctx, body, version=version, components={"schemas": {"A": target}})
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases)


def test_negative_bodies_survive_invalid_keyword_behind_ref_in_any_of(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Filters"}}},
                    },
                    "responses": DEFAULT_RESPONSES,
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "Filters": {
                    "anyOf": [
                        {"type": "integer"},
                        {"$ref": "#/components/schemas/Compound"},
                    ]
                },
                "Compound": {"$recursiveAnchor": True, "type": "string"},
            }
        },
    )
    assert coverage_bodies(schema["/test"]["POST"], GenerationMode.NEGATIVE) == [
        {},
        [None, None],
        None,
        False,
        2.5890419884777833e-42,
    ]


def test_positive_arrays_meet_contains_the_openapi_30_validator_ignores(ctx):
    # Draft 4 has no `contains`, yet a server reading it still expects a matching element.
    body = {
        "type": "array",
        "items": {"type": "integer"},
        "contains": {"type": "integer", "minimum": 10},
        "maxItems": 256,
    }
    operation = body_operation(ctx, body)
    bodies = assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)
    assert [value for value in bodies if not any(item >= 10 for item in value)] == []


def test_positive_body_reaches_max_length_past_the_drawable_limit_for_a_permissive_pattern(ctx):
    length = 16384
    operation = body_operation(ctx, {"type": "string", "pattern": ".*", "maxLength": length})
    assert length in {len(case.body) for case in collect_cases(operation, GenerationMode.POSITIVE)}


@pytest.mark.parametrize(
    ("version", "body"),
    [
        ("3.1.0", {"type": "array", "prefixItems": [{"type": "boolean"}], "items": {"type": "null"}}),
        ("3.1.0", {"type": "array", "items": {"type": "integer"}, "prefixItems": [{"type": "string"}], "minItems": 3}),
        (
            "3.1.0",
            {"type": "object", "properties": {"a": {"type": "null"}}, "propertyNames": {"pattern": "^[0-9]{3}$"}},
        ),
        (
            "3.1.0",
            {
                "type": "object",
                "properties": {"a": {"type": "null"}, "bbb": {"type": "boolean"}},
                "propertyNames": {"minLength": 3},
            },
        ),
        ("3.1.0", {"type": "object", "properties": {"a": {"type": "null"}}, "patternProperties": {"a": False}}),
        (
            "3.1.0",
            {"type": "object", "properties": {"a": {"type": "string"}}, "patternProperties": {"^a$": {"minLength": 5}}},
        ),
        ("3.1.0", {"type": "number", "maximum": 1e30, "multipleOf": 3.0}),
        ("3.1.0", {"type": "number", "exclusiveMaximum": -1e30, "multipleOf": 0.3}),
        (
            "3.0.2",
            {"type": "array", "uniqueItems": True, "maxItems": 3, "items": {"type": "string", "enum": ["a", "b", "c"]}},
        ),
    ],
    ids=[
        "prefix-items-single",
        "prefix-items-padded",
        "property-names-pattern",
        "property-names-min-length",
        "pattern-properties-forbidding",
        "pattern-properties-constraining",
        "multiple-of-past-maximum-precision",
        "multiple-of-past-exclusive-maximum-precision",
        "unique-items-exhausting-enum",
    ],
)
def test_coverage_bodies_match_their_mode(ctx, version, body):
    operation = body_operation(ctx, body, version=version)
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)
    assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases)


@pytest.mark.parametrize(
    ("mode", "body", "schema_kwargs", "assert_kwargs"),
    [
        # Float subtraction drifts off the multiple (`99999.99 - 0.01 = 99999.98000000001`).
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "properties": {"amount": {"type": "number", "minimum": 0, "maximum": 99999.99, "multipleOf": 0.01}},
            },
            {"version": "2.0"},
            {},
            id="positive_number_near_boundary_respects_multiple_of",
        ),
        # Base's `additionalProperties: false` forbids the outer's only optional property.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "additionalProperties": False,
                "allOf": [{"$ref": "#/components/schemas/Base"}],
                "properties": {"properties": {"properties": {"x": {"type": "string"}}}},
            },
            {
                "components": {
                    "schemas": {
                        "Base": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"etag": {"type": "string"}},
                        }
                    }
                }
            },
            {"source": collect_cases},
            id="positive_body_under_allof_with_optional_outer_property_only",
        ),
        # Sibling `oneOf` over `required` makes `a` and `b` mutually exclusive behind a reference.
        pytest.param(
            GenerationMode.POSITIVE,
            {"type": "object", "properties": {"inner": {"$ref": "#/components/schemas/Inner"}}},
            {
                "version": "3.1.0",
                "components": {
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
            },
            {"source": collect_cases},
            id="positive_body_with_sibling_oneof_required_via_ref",
        ),
        # The branch judges the outer schema's own properties as additional.
        pytest.param(
            GenerationMode.POSITIVE,
            {"$ref": "#/components/schemas/Outer"},
            {
                "components": {
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
                }
            },
            {},
            id="all_of_branch_judging_outer_properties_as_additional",
        ),
        # A branch left as a bare reference cannot carry its siblings' constraints.
        pytest.param(
            GenerationMode.POSITIVE,
            {"type": "object", "properties": {"data": {"$ref": "#/components/schemas/Outer"}}},
            {
                "components": {
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
                }
            },
            {},
            id="all_of_branch_that_stays_a_reference",
        ),
        # `pattern` beside a non-string `type` must not yield strings.
        pytest.param(
            GenerationMode.POSITIVE,
            {"type": "number", "pattern": "[0-9]{4}"},
            {},
            {},
            id="coverage_positive_pattern_skipped_for_non_string_type",
        ),
        # A multi-level `allOf` chain keeps `location` from the base schema.
        pytest.param(
            GenerationMode.POSITIVE,
            {"$ref": "#/definitions/Child"},
            {
                "parameters": [{"name": "name", "in": "path", "required": True, "type": "string"}],
                "path": "/resources/{name}",
                "method": "put",
                "version": "2.0",
                "definitions": {
                    "Base": {
                        "properties": {"location": {"type": "string"}, "id": {"type": "string", "readOnly": True}}
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
            },
            {},
            id="coverage_positive_allof_ref_property_merge",
        ),
        # `additionalProperties: {}` allows any extra property.
        pytest.param(
            GenerationMode.NEGATIVE,
            {
                "type": "object",
                "properties": {"params": {"type": "object", "additionalProperties": {}}, "query": {"type": "string"}},
                "required": ["query"],
            },
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_negative_empty_dict_additional_properties_not_treated_as_false",
        ),
        pytest.param(
            GenerationMode.NEGATIVE,
            {
                "type": "object",
                "properties": {"name": {"type": "string", "pattern": "^.{0,99}\\S$", "minLength": 1, "maxLength": 100}},
                "required": ["name"],
            },
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_negative_pattern_with_control_chars_uses_schema_validator",
        ),
        # Values satisfy both `format: uuid` and an uppercase-only pattern.
        pytest.param(
            GenerationMode.POSITIVE,
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
            {},
            {"source": generate_cases},
            id="coverage_positive_body_uuid_format_with_uppercase_pattern",
        ),
        # A lowercase UUID satisfies both keywords, so the required property must not sink the body.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]+$"}},
            },
            {},
            {"source": generate_cases},
            id="coverage_positive_body_required_format_with_wider_pattern",
        ),
        # Every enum value violates `maxLength`, so none can serve as the positive template.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "gender": {"type": "string", "enum": ["MALE", "FEMALE", "UNKNOWN"], "maxLength": 1},
                },
                "required": ["name"],
            },
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_positive_body_skips_properties_with_no_valid_enum_values",
        ),
        # `items` beside `type: object` must not produce a list.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "required": ["value"],
                "properties": {"ids": {"type": "object", "items": {"type": "string"}}, "value": {"type": "string"}},
            },
            {},
            {},
            id="coverage_positive_object_type_with_items",
        ),
        pytest.param(
            GenerationMode.NEGATIVE,
            {
                "type": "object",
                "required": ["version"],
                "properties": {"version": {"type": "string", "enum": ["1.2", "1.3"], "minLength": 3, "maxLength": 3}},
            },
            {},
            {},
            id="coverage_negative_string_length_with_enum",
        ),
        # Length violations stay strings when the type also allows `null`.
        pytest.param(
            GenerationMode.NEGATIVE,
            {"type": "object", "properties": {"name": {"type": ["string", "null"], "maxLength": 10}}},
            {},
            {},
            id="coverage_negative_string_length_nullable",
        ),
        # The anchored pattern holds for `$ref` properties under `additionalProperties: false`.
        pytest.param(
            GenerationMode.POSITIVE,
            {"$ref": "#/components/schemas/TaskRequest"},
            {
                "components": {
                    "schemas": {
                        "TaskRequest": {
                            "type": "object",
                            "required": ["TaskId"],
                            "properties": {"TaskId": {"$ref": "#/components/schemas/BatchLoadTaskId"}},
                            "additionalProperties": False,
                        },
                        "BatchLoadTaskId": {"type": "string", "pattern": "[A-Z0-9]+", "minLength": 3, "maxLength": 32},
                    }
                }
            },
            {},
            id="coverage_positive_body_ref_with_pattern_and_length_constraints",
        ),
        # A `oneOf` branch requires a field defined only in the parent's properties.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "oneOf": [
                    {
                        "additionalProperties": True,
                        "properties": {"status": {"enum": ["completed"]}},
                        "required": ["status", "conclusion"],
                    },
                    {"additionalProperties": True, "properties": {"status": {"enum": ["queued"]}}},
                ],
                "properties": {
                    "name": {"type": "string"},
                    "head_sha": {"type": "string"},
                    "status": {"enum": ["queued", "completed"], "type": "string"},
                    "conclusion": {"enum": ["success", "failure"], "type": "string"},
                },
                "required": ["name", "head_sha"],
                "type": "object",
            },
            {},
            {},
            id="coverage_positive_body_oneof_branch_required_field_missing_from_branch_properties",
        ),
        # Invalid formats stay strings when the type also allows `null`.
        pytest.param(
            GenerationMode.NEGATIVE,
            {"type": "object", "properties": {"email": {"type": ["string", "null"], "format": "email"}}},
            {},
            {},
            id="coverage_negative_format_nullable",
        ),
        pytest.param(
            GenerationMode.NEGATIVE,
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
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_negative_max_length_preserved_when_pattern_has_inner_quantifier",
        ),
        # Optional group with a variable inner part: `maxLength` is not representable in the pattern.
        pytest.param(
            GenerationMode.NEGATIVE,
            {
                "type": "object",
                "required": ["key"],
                "properties": {
                    "key": {
                        "type": "string",
                        "pattern": "^([a-zA-Z0-9!_.*'()-][/a-zA-Z0-9!_.*'()-]*)?$",
                        "minLength": 1,
                        "maxLength": 5,
                    }
                },
            },
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_negative_max_length_preserved_when_outer_optional_group_has_variable_inner",
        ),
        pytest.param(
            GenerationMode.NEGATIVE,
            {
                "type": "object",
                "properties": {"type": {"type": "string"}, "linkedServiceName": {"type": "object"}},
                "additionalProperties": {"type": "object"},
                "required": ["type", "linkedServiceName"],
            },
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_negative_missing_required_with_additional_properties_schema",
        ),
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "required": ["lastName"],
                "properties": {
                    "lastName": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 30,
                        "pattern": "^[a-zA-Z]+([ '-][a-zA-Z]+){0,2}\\.?$",
                        "example": "Franklin",
                    }
                },
            },
            {},
            {"source": generate_cases, "validate_formats": False},
            id="coverage_positive_pattern_with_variable_suffix_not_overconstrained",
        ),
        # Required fields from the second `allOf` reference in a `oneOf` branch are kept.
        pytest.param(
            GenerationMode.POSITIVE,
            {"discriminator": {"propertyName": "product"}, "oneOf": [{"$ref": "#/components/schemas/SMS"}]},
            {
                "components": {
                    "schemas": {
                        "SMS": {
                            "allOf": [
                                {"$ref": "#/components/schemas/base_request"},
                                {"$ref": "#/components/schemas/sms_fields"},
                            ]
                        },
                        "base_request": {
                            "type": "object",
                            "properties": {"product": {"type": "string"}, "account_id": {"type": "string"}},
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
                }
            },
            {},
            id="coverage_positive_body_nested_allof_inner_required_preserved",
        ),
        # The `const: null` branch is excluded by the sibling `type`.
        pytest.param(
            GenerationMode.POSITIVE,
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
            {"version": "3.1.0"},
            {},
            id="coverage_positive_body_anyof_const_null_excluded_by_sibling_type",
        ),
        pytest.param(
            GenerationMode.NEGATIVE,
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
                                                "role": {"type": "string", "enum": ["user", "assistant"]},
                                                "content": {"oneOf": [{"type": "string"}, {"type": "array"}]},
                                                "type": {"type": "string", "enum": ["message"]},
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
            {},
            {},
            id="coverage_array_above_max_items_with_complex_items_schema",
        ),
        # ECMA-262 without the `u` flag allows identity escapes such as `\-`.
        pytest.param(
            GenerationMode.POSITIVE,
            {"type": "object", "properties": {"latitude": {"type": "string", "pattern": "^\\-?\\d+$"}}},
            {},
            {},
            id="coverage_pattern_with_identity_escape_in_body",
        ),
        # `host` is required but has no definition in `properties`.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "required": ["name", "host"],
                "properties": {"name": {"type": "string"}, "port": {"type": "integer"}},
            },
            {},
            {},
            id="required_property_not_in_properties_is_generated",
        ),
        # `false` in a string enum, as YAML parses a bare `NO`.
        pytest.param(
            GenerationMode.POSITIVE,
            {"type": "object", "properties": {"country": {"type": "string", "enum": ["US", "GB", False]}}},
            {},
            {},
            id="invalid_enum_values_excluded_from_positive_cases",
        ),
        # `false` in a string items enum, as YAML parses a bare `NO`.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "properties": {
                    "countries": {"type": "array", "items": {"type": "string", "enum": ["US", "GB", False]}}
                },
            },
            {},
            {},
            id="invalid_enum_items_excluded_from_positive_array_cases",
        ),
        # Outer properties without an explicit type beside an `allOf` that declares required fields.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "allOf": [{"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}],
                "properties": {"details": {"properties": {"key": {"type": "string"}}}},
            },
            {"method": "put"},
            {},
            id="allof_with_outer_properties_includes_required_fields",
        ),
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "allOf": [{"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}],
                "properties": {"details": {"properties": {"key": {"type": "string"}}}},
            },
            {"method": "put"},
            {},
            id="allof_with_explicit_type_object_includes_required_fields",
        ),
        # Azure's `7.00:00:00` default is not an ISO 8601 duration, so it must not become a constant.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "properties": {
                    "constraints": {
                        "type": "object",
                        "properties": {
                            "maxWallClockTime": {"type": "string", "format": "duration", "default": "7.00:00:00"}
                        },
                    }
                },
            },
            {"method": "put"},
            {},
            id="format_invalid_default_not_used_as_const",
        ),
        # `maxItems: 0` permits only `[]`.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "properties": {
                    "stacks": {"type": "array", "maxItems": 0, "items": {"type": "string", "enum": ["unknown"]}}
                },
            },
            {},
            {},
            id="positive_array_with_maxitems_zero",
        ),
        # The `not` flip must still satisfy the outer property types.
        pytest.param(
            GenerationMode.POSITIVE,
            {
                "type": "object",
                "properties": {"image_url": {"type": "string"}, "file_id": {"type": "string"}},
                "anyOf": [{"required": ["image_url"]}, {"required": ["file_id"]}],
                "not": {"required": ["image_url", "file_id"]},
                "additionalProperties": False,
            },
            {"version": "3.1.0"},
            {},
            id="positive_not_flip_validates_against_outer_constraints",
        ),
        # `minLength`/`maxLength` do not apply to an integer.
        pytest.param(
            GenerationMode.NEGATIVE,
            {"type": "object", "properties": {"ttl": {"type": "integer", "minLength": 30, "maxLength": 3600}}},
            {},
            {},
            id="minlength_maxlength_negative_skipped_for_integer_type",
        ),
        # `additionalProperties: false` beside `allOf` judges the branch's own property names too.
        pytest.param(
            GenerationMode.POSITIVE,
            {"type": "object", "additionalProperties": False, "allOf": [{"$ref": "#/components/schemas/Inner"}]},
            {
                "components": {
                    "schemas": {
                        "Inner": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                        }
                    }
                }
            },
            {},
            id="single_branch_allof_keeps_outer_additional_properties",
        ),
    ],
)
def test_coverage_bodies_match_a_single_mode(ctx, mode, body, schema_kwargs, assert_kwargs):
    operation = body_operation(ctx, body, **schema_kwargs)
    assert_bodies(operation, mode, valid=mode == GenerationMode.POSITIVE, **assert_kwargs)


def test_positive_body_drops_every_any_of_shape_the_parent_forbids(ctx):
    # Each branch proposes a key `additionalProperties: false` rejects, leaving only `null`.
    body = {
        "type": "object",
        "required": ["data"],
        "properties": {
            "data": {
                "additionalProperties": False,
                "anyOf": [
                    {"properties": {"last_name": {"type": "string"}}, "required": ["last_name"]},
                    {"properties": {"nickname": {"type": "string"}}, "required": ["nickname"]},
                ],
            }
        },
    }
    operation = body_operation(ctx, body)
    assert assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases) == [{"data": None}]


def test_negative_const_body_under_openapi_31(ctx):
    operation = body_operation(ctx, {"const": 42}, version="3.1.0")
    assert assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases) == [
        {},
        [None, None],
        None,
        False,
        2.5890419884777833e-42,
        "AAA",
    ]


def test_additional_property_name_skips_a_declared_one(ctx):
    body = {
        "type": "object",
        "properties": {"x-schemathesis-additional": {"type": "string"}},
        "additionalProperties": {"type": "integer"},
    }
    operation = body_operation(ctx, body)
    bodies = assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)
    assert {"x-schemathesis-additional": "", "x-schemathesis-additional1": 0} in bodies


def test_no_unexpected_property_when_every_candidate_name_matches_a_property_pattern(ctx):
    # A name matching `patternProperties` is judged by that pattern, so it cannot break `additionalProperties: false`.
    body = {"type": "object", "patternProperties": {"property": {"type": "string"}}, "additionalProperties": False}
    operation = body_operation(ctx, body)
    assert assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases) == [
        {"property": {}},
        {"property": [None, None]},
        {"property": None},
        {"property": False},
        {"property": 0},
        [None, None],
        "AAA",
        None,
        False,
        0,
    ]


def test_no_pattern_violation_for_properties_sharing_a_pattern_every_string_matches(ctx):
    inner = {"type": "string", "minLength": 1, "pattern": "[\\s\\S]"}
    operation = body_operation(ctx, {"type": "object", "properties": {"alpha": inner, "beta": dict(inner)}})
    bodies = assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases)
    assert [body for body in bodies if isinstance(body, dict) and "" not in body.values()] == [
        {"alpha": "0", "beta": {}},
        {"alpha": "0", "beta": [None, None]},
        {"alpha": "0", "beta": None},
        {"alpha": "0", "beta": False},
        {"alpha": "0", "beta": 0},
        {"alpha": {}, "beta": "0"},
        {"alpha": [None, None], "beta": "0"},
        {"alpha": None, "beta": "0"},
        {"alpha": False, "beta": "0"},
        {"alpha": 0, "beta": "0"},
    ]


def test_negative_format_bodies_for_properties_sharing_a_format(ctx):
    body = {
        "type": "object",
        "properties": {"a": {"type": "string", "format": "ipv4"}, "b": {"type": "string", "format": "ipv4"}},
    }
    operation = body_operation(ctx, body)
    bodies = assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases)
    assert [body for body in bodies if isinstance(body, dict) and "" in body.values()] == [
        {"a": "0.0.0.0", "b": ""},
        {"a": "", "b": "0.0.0.0"},
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"type": "string", "format": "hostname", "pattern": "^[a-z]+$"}, ["", {}, [None, None], None, False, 0]),
        (
            {"type": "number", "format": "float", "maximum": 3.4028234663852886e38},
            [{}, [None, None], "AAA", None, False],
        ),
        ({"type": "number", "format": "float", "minimum": 0.1}, [-0.9, {}, [None, None], "AAA", None, False]),
    ],
    ids=["hostname-every-pattern-match-is-valid", "float-maximum-at-float32-limit", "float-minimum"],
)
def test_negative_bodies_for_formats_with_narrow_violations(ctx, body, expected):
    operation = body_operation(ctx, body)
    assert assert_bodies(operation, GenerationMode.NEGATIVE, valid=False, source=collect_cases) == expected


def test_no_invalid_format_query_value_when_its_pattern_has_no_python_spelling(ctx):
    parameter = {
        "in": "query",
        "name": "q",
        "required": True,
        "schema": {"type": "string", "format": "date", "pattern": "\\p{Tibetan}"},
    }
    operation = load_schema(ctx, parameters=[parameter], method="get", version="3.1.0")["/foo"]["GET"]
    assert [case.query for case in collect_cases(operation, GenerationMode.NEGATIVE)] == [{}] * 9


def targeted_values(operation, mode, container, name):
    return [
        (getattr(case, container) or {}).get(name)
        for case in collect_cases(operation, mode)
        if case.meta.phase.data.parameter == name
    ]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "string", "format": "email", "minLength": 6},
            [None, "0@0.a", "000000", ["null", "null"], "null", "true", "0.5"],
        ),
        (
            {"type": "string", "minLength": 1, "pattern": "a\\Z"},
            [None, "0", "", ["null", "null"], "null", "true", "0.5"],
        ),
        ({"type": "string"}, [None]),
    ],
    ids=["min-length-below-every-email", "pattern-unreadable-by-the-validator", "every-string-accepted"],
)
def test_negative_query_values(ctx, schema, expected):
    operation = load_schema(
        ctx, parameters=[{"in": "query", "name": "q", "required": True, "schema": schema}], method="get"
    )["/foo"]["GET"]
    assert targeted_values(operation, GenerationMode.NEGATIVE, "query", "q") == expected


def test_negative_query_value_past_the_generation_buffer(ctx):
    schema = {"type": "string", "maxLength": 65536}
    operation = load_schema(
        ctx, parameters=[{"in": "query", "name": "q", "required": True, "schema": schema}], method="get"
    )["/foo"]["GET"]
    assert targeted_values(operation, GenerationMode.NEGATIVE, "query", "q") == [None, "a" * 65537]


@pytest.mark.parametrize(
    ("location", "pattern", "path"),
    [
        ("path", "arn:aws:kinesisvideo:[a-z0-9-]+:[0-9]+:[a-z]+/[a-zA-Z0-9_.-]+/[0-9]+", "/foo/{p}"),
        ("header", "arn:[a-z0-9-\\.]{1,63}:[a-z0-9-\\.]{0,63}", "/foo"),
    ],
    ids=["path-pattern-with-literal-slash", "header-pattern-requiring-a-colon"],
)
def test_no_positive_case_when_the_pattern_needs_characters_the_location_cannot_carry(ctx, location, pattern, path):
    schema = {"type": "string", "pattern": pattern, "minLength": 1, "maxLength": 1024}
    operation = load_schema(
        ctx, parameters=[{"in": location, "name": "p", "required": True, "schema": schema}], method="get", path=path
    )[path]["GET"]
    with pytest.raises(pytest.fail.Exception, match="generated no cases"):
        collect_cases(operation, GenerationMode.POSITIVE)


@pytest.mark.parametrize(
    ("header_schema", "expected"),
    [
        (
            {"type": "string", "enum": ["text/plain"]},
            [("text/plain", "text/plain"), ("text/plain", "application/json")],
        ),
        (
            {"type": "string", "enum": ["application/xml"]},
            [("application/xml", "text/plain"), ("application/xml", "application/json")],
        ),
    ],
    ids=["pinned-to-the-media-type-the-header-admits", "declared-value-kept"],
)
def test_positive_content_type_header_parameter_follows_the_body_media_types(ctx, header_schema, expected):
    operation = load_schema(
        ctx,
        parameters=[{"in": "header", "name": "Content-Type", "required": True, "schema": header_schema}],
        request_body={
            "required": True,
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "text/plain": {"schema": {"type": "string"}},
            },
        },
    )["/foo"]["post"]
    assert sorted(
        (case.headers["Content-Type"], case.media_type) for case in collect_cases(operation, GenerationMode.POSITIVE)
    ) == sorted(expected)


def positive_queries(operation):
    return [case.query for case in collect_cases(operation, GenerationMode.POSITIVE)]


EMPTY_ENUM = {"type": "string", "enum": []}


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        (
            [
                {"in": "query", "name": "a", "schema": {"type": "string"}},
                {"in": "query", "name": "b", "schema": EMPTY_ENUM},
                {"in": "query", "name": "c", "schema": EMPTY_ENUM},
            ],
            [{}, {"a": ""}],
        ),
        (
            [
                {"in": "query", "name": "a", "schema": {"type": "string"}},
                {"in": "query", "name": "b", "schema": {"type": "string"}},
                {"in": "query", "name": "c", "schema": EMPTY_ENUM},
            ],
            [{}, {"b": ""}, {"a": ""}, {"a": "", "b": ""}],
        ),
        (
            [
                {"in": "query", "name": name, "schema": {"type": "array", "items": {"type": "string"}, "maxItems": 0}}
                for name in "abc"
            ],
            [{"a": [], "b": [], "c": []}],
        ),
        ([{"in": "query", "name": name, "schema": EMPTY_ENUM} for name in "abc"], [{}]),
    ],
    ids=[
        "optional-parameters-without-values",
        "one-optional-parameter-without-a-value",
        "combinations-identical-on-the-wire",
        "no-optional-parameter-has-a-value",
    ],
)
def test_positive_optional_query_combinations_skip_repeated_requests(ctx, parameters, expected):
    operation = load_schema(ctx, parameters=parameters, method="get")["/foo"]["GET"]
    assert positive_queries(operation) == expected


def test_no_positive_case_when_the_required_body_admits_nothing(ctx):
    operation = load_schema(
        ctx,
        parameters=[{"in": "query", "name": "q", "required": True, "schema": {"type": "integer", "minimum": 1}}],
        body={"type": "string", "enum": [1]},
    )["/foo"]["post"]
    with pytest.raises(pytest.fail.Exception, match="generated no cases"):
        collect_cases(operation, GenerationMode.POSITIVE)


def test_positive_body_for_repeated_consumes_entry_is_not_sent_twice(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {"type": "object", "properties": {"a": {"type": "integer"}}},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
        consumes=["application/json", "application/json"],
    )
    operation = schemathesis.openapi.from_dict(raw_schema)["/foo"]["POST"]
    assert [(case.media_type, case.body) for case in collect_cases(operation, GenerationMode.POSITIVE)] == [
        ("application/json", {}),
        ("application/json", {"a": 0}),
    ]


def test_parameter_with_an_always_true_not_still_gets_a_value(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {"in": "path", "name": "p", "required": True, "schema": {"type": "string", "not": False}},
            {"in": "header", "name": "X-A", "required": True, "schema": {"type": "string", "not": False}},
        ],
        method="get",
        path="/foo/{p}",
        version="3.1.0",
    )["/foo/{p}"]["GET"]
    assert [(case.path_parameters, case.headers) for case in collect_cases(operation, GenerationMode.POSITIVE)] == [
        ({"p": "0"}, {"X-A": ""})
    ]


def test_negative_query_array_with_items_the_validator_cannot_load(ctx):
    # A value JSON cannot hold, such as YAML `!!binary`, keeps the validator from loading, so every negative ships.
    schema = {"type": "array", "items": {"type": "string", "x-raw": b"\x00"}}
    operation = load_schema(
        ctx, parameters=[{"in": "query", "name": "ids", "required": True, "schema": schema}], method="get"
    )["/foo"]["GET"]
    assert targeted_values(operation, GenerationMode.NEGATIVE, "query", "ids") == [[], "AAA", "null", "false"]


def test_content_type_header_parameter_pinned_to_the_body_media_type_when_its_schema_cannot_judge(ctx):
    operation = load_schema(
        ctx,
        parameters=[{"in": "header", "name": "Content-Type", "required": True, "schema": True}],
        body={"type": "object"},
        version="3.1.0",
    )["/foo"]["post"]
    assert [(case.headers, case.body) for case in collect_cases(operation, GenerationMode.POSITIVE)] == [
        ({"Content-Type": "application/json"}, {})
    ]


def test_custom_encoded_multipart_body_in_optional_request_body(ctx):
    schemathesis.openapi.media_type("image/png", st.just(b"\x89PNG"))
    operation = load_schema(
        ctx,
        request_body={
            "content": {
                "multipart/form-data": {
                    "schema": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}},
                    "encoding": {"file": {"contentType": "image/png"}},
                }
            }
        },
    )["/foo"]["post"]
    assert [
        (case.headers, case.body) for case in collect_cases(operation, GenerationMode.NEGATIVE) if case.headers
    ] == [
        ({"Content-Type": "text/plain"}, {"file": b"\x89PNG"}),
        ({"Content-Type": "multipart/form-data"}, NOT_SET),
    ]


def test_custom_encoded_multipart_body_does_not_replace_an_earlier_body(ctx):
    schemathesis.openapi.media_type("image/png", st.just(b"\x89PNG"))
    operation = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "multipart/form-data": {
                    "schema": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}},
                    "encoding": {"file": {"contentType": "image/png"}},
                },
            },
        },
    )["/foo"]["post"]
    assert [(case.media_type, case.body) for case in collect_cases(operation, GenerationMode.POSITIVE)] == [
        ("multipart/form-data", {"file": b"\x89PNG"}),
        ("application/json", {}),
    ]


def test_custom_media_type_body_in_optional_request_body(ctx):
    schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
    operation = load_schema(
        ctx, request_body={"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}
    )["/foo"]["post"]
    assert [(case.media_type, case.body) for case in collect_cases(operation, GenerationMode.POSITIVE)] == [
        ("application/pdf", b"%PDF-1.4")
    ]


def test_no_positive_custom_media_type_body_beside_a_required_parameter_without_values(ctx):
    schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
    operation = load_schema(
        ctx,
        parameters=[{"in": "query", "name": "q", "required": True, "schema": EMPTY_ENUM}],
        request_body={
            "required": True,
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
    )["/foo"]["post"]
    with pytest.raises(pytest.fail.Exception, match="generated no cases"):
        collect_cases(operation, GenerationMode.POSITIVE)


def coverage_bodies(operation, mode):
    try:
        cases = collect_cases(operation, mode)
    except pytest.fail.Exception:
        return []
    return [case.body for case in cases if body_mode(case) == mode and case.media_type is not None]


BODY_TYPE_VIOLATIONS = [[None, None], "AAA", None, False, 0]


@pytest.mark.parametrize(
    ("version", "body", "components", "positive", "negative"),
    [
        (
            "3.0.2",
            {
                "type": "object",
                "properties": {"ab": {"type": "integer"}},
                "patternProperties": {"^\\p{L}$": {"type": "string"}},
                "required": ["ab"],
            },
            None,
            [{"ab": 0}],
            [
                {},
                {"ab": {}},
                {"ab": [None, None]},
                {"ab": "AAA"},
                {"ab": None},
                {"ab": False},
                {"ab": 2.5890419884777833e-42},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.0.2",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "string", "pattern": "^a+$", "minLength": 2},
                    "b": {"type": "string", "pattern": "^a+$", "minLength": 2, "title": "x"},
                },
                "required": ["a", "b"],
            },
            None,
            [{"a": "aa", "b": "aaa"}, {"a": "aaa", "b": "aa"}, {"a": "aa", "b": "aa"}],
            [
                {"a": "aa"},
                {"b": "aa"},
                {"a": "aa", "b": "a"},
                {"a": "aa", "b": "00"},
                {"a": "aa", "b": {}},
                {"a": "aa", "b": [None, None]},
                {"a": "aa", "b": None},
                {"a": "aa", "b": False},
                {"a": "aa", "b": 0},
                {"a": "a", "b": "aa"},
                {"a": "00", "b": "aa"},
                {"a": {}, "b": "aa"},
                {"a": [None, None], "b": "aa"},
                {"a": None, "b": "aa"},
                {"a": False, "b": "aa"},
                {"a": 0, "b": "aa"},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.0.2",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "a": {"$ref": "#/components/schemas/X"},
                    "c": {"type": "string", "pattern": "\\p{Tibetan}"},
                },
                "required": ["a"],
            },
            {"X": {"type": "integer", "minimum": 5}},
            [{"a": 6}, {"a": 5}],
            [
                {},
                {"a": 4},
                {"a": {}},
                {"a": [None, None]},
                {"a": "AAA"},
                {"a": None},
                {"a": False},
                {"a": 2.5890419884777833e-42},
                {"a": 5, "x-schemathesis-unknown-property": 42},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.0.2",
            {
                "type": "array",
                "items": {"type": "object", "properties": {"a": {"type": "string", "pattern": "(?<=a+)b"}}},
            },
            None,
            [[], [{}]],
            [[[None, None]], ["AAA"], [None], [False], [0], {}, "AAA", None, False, 0],
        ),
        (
            "3.1.0",
            {"type": "object", "properties": {"a": {"anyOf": [False, {"type": "integer"}]}}, "required": ["a"]},
            None,
            [{"a": 0}],
            [
                {},
                {"a": {}},
                {"a": [None, None]},
                {"a": "AAA"},
                {"a": None},
                {"a": False},
                {"a": 2.5890419884777833e-42},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.1.0",
            {
                "type": "object",
                "properties": {"a": {"oneOf": [{"$ref": "#/components/schemas/F"}, {"type": "integer"}]}},
                "required": ["a"],
            },
            {"F": False},
            [{"a": 0}],
            [
                {},
                {"a": "AAA"},
                {"a": 2.5890419884777833e-42},
                {"a": {}},
                {"a": [None, None]},
                {"a": ""},
                {"a": False},
                {"a": True},
                {"a": None},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.1.0",
            {"allOf": [True, {"type": "object", "properties": {"a": {"type": "integer"}}}]},
            None,
            [{}, {"a": 0}],
            [
                {"a": {}},
                {"a": [None, None]},
                {"a": "AAA"},
                {"a": None},
                {"a": False},
                {"a": 2.5890419884777833e-42},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.1.0",
            {
                "allOf": [
                    {"$ref": "#/components/schemas/T"},
                    {"type": "object", "properties": {"a": {"type": "integer"}}},
                ]
            },
            {"T": True},
            [{}],
            [
                {"a": {}},
                {"a": [None, None]},
                {"a": "AAA"},
                {"a": None},
                {"a": False},
                {"a": 2.5890419884777833e-42},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.0.2",
            {
                "allOf": [
                    {"type": "object", "additionalProperties": False},
                    {"type": "object", "additionalProperties": False},
                ]
            },
            None,
            [{}],
            [*BODY_TYPE_VIOLATIONS, {"x-schemathesis-unknown-property": 42}],
        ),
        (
            "3.0.2",
            {
                "type": "object",
                "properties": {"a": {"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]+$", "maxLength": 200}},
                "required": ["a"],
            },
            None,
            None,
            [
                {},
                {"a": ""},
                {"a": "0"},
                {"a": {}},
                {"a": [None, None]},
                {"a": None},
                {"a": False},
                {"a": 0},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.0.2",
            {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "array",
                        "items": {"type": ["integer", "string"]},
                        "contains": {"type": "integer"},
                        "maxContains": 1,
                        "minItems": 3,
                    }
                },
                "required": ["a"],
            },
            None,
            [{"a": [0, "", "", ""]}, {"a": [0, "", ""]}],
            None,
        ),
        (
            "3.1.0",
            {"type": "object", "additionalProperties": {"type": "integer"}, "propertyNames": False},
            None,
            [{}],
            BODY_TYPE_VIOLATIONS,
        ),
        (
            "3.0.2",
            {"type": "object", "additionalProperties": {"type": "integer"}, "propertyNames": {"not": {}}},
            None,
            [{}],
            BODY_TYPE_VIOLATIONS,
        ),
        (
            "3.0.2",
            {"type": "object", "maxProperties": 0, "propertyNames": {"not": {}}},
            None,
            [{}],
            BODY_TYPE_VIOLATIONS,
        ),
        (
            "3.0.2",
            {"type": "object", "properties": {"a": {"type": "integer", "examples": ["x"]}}},
            None,
            [{}, {"a": 0}],
            [
                {"a": {}},
                {"a": [None, None]},
                {"a": "AAA"},
                {"a": None},
                {"a": False},
                {"a": 2.5890419884777833e-42},
                *BODY_TYPE_VIOLATIONS,
            ],
        ),
        (
            "3.0.2",
            {"type": "number", "format": "float", "maximum": 3.4028234663852886e38, "minimum": -3.4028234663852886e38},
            None,
            [3.4028234663852886e38, -3.4028234663852886e38],
            [{}, [None, None], "AAA", None, False],
        ),
        (
            "3.0.2",
            {"type": "number", "format": "float", "maximum": 1.5, "minimum": -1.5},
            None,
            [0.5, 1.5, -0.5, -1.5],
            [-2.5, 2.5, {}, [None, None], "AAA", None, False],
        ),
        (
            "3.1.0",
            {"type": "array", "prefixItems": [{"type": "integer"}], "items": {"type": "string"}, "uniqueItems": True},
            None,
            [[]],
            [
                [0, 0],
                [{}],
                [[None, None]],
                ["AAA"],
                [None],
                [False],
                [2.5890419884777833e-42],
                {},
                "AAA",
                None,
                False,
                0,
            ],
        ),
        ("3.0.2", {"enum": []}, None, [], ["AAA"]),
        ("3.0.2", {"type": "string", "enum": ["AAA"]}, None, ["AAA"], [-58800, {}, [None, None], None, False, 0]),
        (
            "3.1.0",
            {"type": "object", "propertyNames": {"type": "string", "maxLength": 2}},
            None,
            [{}],
            [{"000": ""}, *BODY_TYPE_VIOLATIONS],
        ),
        (
            "3.1.0",
            {"type": "array", "prefixItems": [{"not": {}}, {"type": "integer"}]},
            None,
            [[]],
            [{}, "AAA", None, False, 0],
        ),
        ("3.0.2", {"type": "string", "multipleOf": 2}, None, [""], [{}, [None, None], None, False, 0]),
        ("3.0.2", {"type": "array", "uniqueItems": False}, None, [[]], [{}, "AAA", None, False, 0]),
        (
            "3.0.2",
            {"type": "string", "uniqueItems": True, "not": {"minItems": 2}},
            None,
            None,
            [["null", "null"], {}, [None, None], None, False, 0],
        ),
        ("3.0.2", {"type": "string", "format": "iri"}, None, ["https://0.com"], [{}, [None, None], None, False, 0]),
        (
            "3.1.0",
            {"type": "integer", "minimum": 6, "exclusiveMinimum": 5},
            None,
            [7, 6],
            [5, {}, [None, None], "AAA", None, False, 2.5890419884777833e-42],
        ),
        (
            "3.0.2",
            {"type": "string", "minLength": 2, "not": {"maxLength": 1}},
            None,
            ["000", "00"],
            [{}, [None, None], None, False, 0],
        ),
        ("3.0.2", {"type": "array", "maxItems": 40000}, None, [[]], [{}, "AAA", None, False, 0]),
        (
            "3.0.2",
            {"type": "array", "maxItems": 20},
            None,
            [[None] * 19, [None] * 20, []],
            [[None] * 21, {}, "AAA", None, False, 0],
        ),
        (
            "3.0.2",
            {"type": "array", "maxItems": 1, "items": {"not": {}}},
            None,
            [[]],
            [[{}], [[None, None]], [0], [""], [False], [True], [None], {}, "AAA", None, False, 0],
        ),
        (
            "3.0.2",
            {"type": "array", "maxItems": 1, "uniqueItems": True, "items": {"not": {}}},
            None,
            [[]],
            [[{}], [[None, None]], [0], [""], [False], [True], [None], {}, "AAA", None, False, 0],
        ),
        ("3.0.2", {"type": "object", "minProperties": 0}, None, [{}], BODY_TYPE_VIOLATIONS),
        # Draft 4 does not know `contains`, so a malformed one is ignored, as fuzzing does.
        (
            "3.0.2",
            {"type": "array", "contains": {"type": "string", "maxLength": -1, "enum": ["x"]}},
            None,
            [[]],
            [{}, "AAA", None, False, 0],
        ),
        (
            "3.0.2",
            {"type": "array", "contains": {"type": "string", "pattern": "(?u)^a+$"}},
            None,
            [],
            [{}, "AAA", None, False, 0],
        ),
    ],
    ids=[
        "pattern-properties-python-cannot-read",
        "properties-sharing-a-pattern",
        "ref-property-beside-an-unbuildable-optional-one",
        "items-with-a-lookbehind-pattern",
        "any-of-with-a-false-branch",
        "one-of-with-a-reference-to-false",
        "all-of-with-a-true-branch",
        "all-of-with-a-reference-to-true",
        "all-of-closed-objects",
        "uuid-with-a-pattern-and-a-max-length",
        "max-contains",
        "property-names-false",
        "property-names-rejecting-every-name",
        "max-properties-zero-and-no-valid-names",
        "property-example-violating-its-schema",
        "float-at-the-float32-limits",
        "float-within-bounds",
        "prefix-items-with-unique-items",
        "empty-enum",
        "enum-of-the-default-invalid-value",
        "property-names-max-length",
        "prefix-items-starting-with-false",
        "string-multiple-of",
        "unique-items-false",
        "string-with-array-keywords",
        "iri-format-under-openapi-30",
        "exclusive-minimum-equal-to-the-value-below-minimum",
        "min-length-contradicted-by-not",
        "max-items-past-the-generation-buffer",
        "max-items-past-the-drawn-limit-without-items",
        "max-items-with-false-items",
        "max-items-with-false-unique-items",
        "min-properties-zero",
        "contains-with-an-invalid-max-length",
        "contains-with-an-inline-unicode-flag-pattern",
    ],
)
def test_coverage_bodies(ctx, version, body, components, positive, negative):
    extra = {"components": {"schemas": components}} if components else {}
    operation = body_operation(ctx, body, version=version, **extra)
    if positive is not None:
        assert coverage_bodies(operation, GenerationMode.POSITIVE) == positive
    if negative is not None:
        assert coverage_bodies(operation, GenerationMode.NEGATIVE) == negative


@pytest.mark.parametrize(
    ("version", "schema"),
    [
        ("3.0.2", {"type": "array", "items": {"type": "object", "minProperties": 1}}),
        ("3.1.0", {"type": "array", "prefixItems": [{"type": "object", "minProperties": 1}]}),
    ],
    ids=["items", "prefix-items"],
)
def test_no_empty_object_item_negative_for_an_optional_query_array(ctx, version, schema):
    # `q=` with an empty object serializes to nothing, which is a valid request for an optional parameter.
    operation = load_schema(
        ctx, parameters=[{"in": "query", "name": "q", "schema": schema}], method="get", version=version
    )["/foo"]["GET"]
    assert targeted_values(operation, GenerationMode.NEGATIVE, "query", "q") == [
        [["null", "null"]],
        ["0"],
        "AAA",
        "null",
        "true",
        "0.5",
    ]


def test_negative_query_value_for_a_type_union(ctx):
    schema = {"type": ["boolean", "integer"]}
    operation = load_schema(
        ctx,
        parameters=[{"in": "query", "name": "q", "required": True, "schema": schema}],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert targeted_values(operation, GenerationMode.NEGATIVE, "query", "q") == [None, ["null", "null"], "AAA", "null"]


def test_form_body_with_a_false_any_of_branch(ctx):
    body = {
        "type": "object",
        "properties": {"a": {"anyOf": [{"type": "integer", "maximum": 5}, False]}},
        "required": ["a"],
    }
    operation = body_operation(ctx, body, version="3.1.0", media_type="application/x-www-form-urlencoded")
    assert coverage_bodies(operation, GenerationMode.POSITIVE) == [{"a": 4}, {"a": 5}, {"a": 0}]
    assert coverage_bodies(operation, GenerationMode.NEGATIVE) == [{}, {"a": 6}, {"a": "AAA"}, {"a": True}, {"a": 0.5}]


def test_positive_body_drawn_past_a_double_negation(ctx):
    # The generator cannot follow `not: {not: ...}`, so it draws from the wider schema and keeps what the validator admits.
    body = {
        "type": "object",
        "properties": {"a": {"type": "string", "maxLength": 3, "not": {"not": {"minLength": 2}}}},
        "required": ["a"],
    }
    operation = body_operation(ctx, body)
    assert assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


def test_positive_body_string_longer_than_a_pattern_can_draw(ctx):
    body = {
        "type": "object",
        "properties": {"a": {"type": "string", "pattern": "^[a-z]+$", "minLength": 9000}},
        "required": ["a"],
    }
    operation = body_operation(ctx, body)
    assert [len(body["a"]) for body in coverage_bodies(operation, GenerationMode.POSITIVE)] == [9000]
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


def test_positive_body_string_longer_than_a_unicode_flag_pattern_can_draw(ctx):
    body = {
        "type": "object",
        "properties": {"a": {"type": "string", "pattern": "(?u)^a+$", "minLength": 9000}},
        "required": ["a"],
    }
    operation = body_operation(ctx, body, version="3.1.0")
    assert [len(body["a"]) for body in coverage_bodies(operation, GenerationMode.POSITIVE)] == [9000]
    assert_bodies(operation, GenerationMode.POSITIVE, valid=True, source=collect_cases)


def test_negative_body_below_min_properties_past_the_drawn_limit(ctx):
    operation = body_operation(ctx, {"type": "object", "minProperties": 300, "properties": {"x0": {"type": "integer"}}})
    below = [
        body
        for body in coverage_bodies(operation, GenerationMode.NEGATIVE)
        if isinstance(body, dict) and len(body) == 299
    ]
    assert below == [{f"x{index}": None for index in range(1, 300)}]


def test_positive_query_values_for_a_parameter_accepting_anything(ctx):
    operation = load_schema(
        ctx,
        parameters=[
            {"in": "query", "name": "a", "required": True, "schema": True},
            {"in": "query", "name": "b", "schema": {"type": "integer"}},
        ],
        method="get",
        version="3.1.0",
    )["/foo"]["GET"]
    assert positive_queries(operation) == [
        {"a": "null"},
        {"a": {}, "b": "0"},
        {"a": ["null", "null"], "b": "0"},
        {"a": "0", "b": "0"},
        {"a": "", "b": "0"},
        {"a": "false", "b": "0"},
        {"a": "true", "b": "0"},
        {"a": "null", "b": "0"},
    ]
    assert targeted_values(operation, GenerationMode.NEGATIVE, "query", "b") == [
        ["null", "null"],
        "AAA",
        "null",
        "true",
    ]


def _ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


PROPERTY = {"type": "object", "required": ["property"], "properties": {"property": {"type": "string"}}}
# A trimmed CQL2 grammar: `Func` arguments recurse through `$dynamicRef`, which Draft 4 reads as "anything".
CQL2_LIKE = {
    "Expr": {"$dynamicAnchor": "expr", "oneOf": [_ref("Cmp"), _ref("Func"), {"type": "boolean"}]},
    "Cmp": {
        "type": "object",
        "required": ["op", "args"],
        "properties": {
            "op": {"enum": ["="]},
            "args": {"type": "array", "minItems": 2, "maxItems": 2, "items": _ref("Scalar")},
        },
    },
    "Scalar": {"oneOf": [_ref("Casei"), _ref("Func"), _ref("Prop")]},
    "Casei": {
        "type": "object",
        "required": ["op", "args"],
        "properties": {
            "op": {"enum": ["casei"]},
            "args": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"oneOf": [_ref("Prop"), _ref("Func")]}},
        },
    },
    "Func": {
        "type": "object",
        "required": ["op", "args"],
        "properties": {
            "op": {"type": "string", "not": {"enum": ["=", "casei"]}},
            "args": {"type": "array", "items": {"oneOf": [_ref("Prop"), {"$dynamicRef": "#expr"}]}},
        },
    },
    "Prop": PROPERTY,
}


def test_negative_bodies_are_rejected_under_every_draft(ctx):
    # A 3.0 body can reference a grammar written for a newer draft; a negative only one reading
    # rejects is a valid request under the other.
    operation = body_operation(ctx, _ref("Expr"), version="3.0.2", components={"schemas": CQL2_LIKE})
    document = json.loads(
        json.dumps({"$ref": "#/$defs/Expr", "$defs": CQL2_LIKE}).replace("#/components/schemas/", "#/$defs/")
    )
    judges = [
        body_validator(operation, validator_cls=jsonschema_rs.Draft4Validator),
        jsonschema_rs.Draft202012Validator(document),
    ]

    bodies = [case.body for case in iter_cases(operation, GenerationMode.NEGATIVE) if case.body is not NOT_SET]

    assert bodies, "No negative bodies generated"
    assert [value for value in bodies if any(judge.is_valid(value) for judge in judges)] == []


def test_coverage_pattern_with_identity_escape_in_query(ctx):
    schema = build_schema(
        ctx,
        [{"in": "query", "name": "latitude", "schema": {"type": "string", "pattern": r"^\-?\d+$"}, "required": True}],
    )

    assert_positive_coverage(schema, [{"query": {"latitude": "0"}}])
