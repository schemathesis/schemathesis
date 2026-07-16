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
from schemathesis.config import ChecksConfig, SanitizationConfig
from schemathesis.config._projects import ProjectConfig
from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import AcceptedNegativeData
from schemathesis.core.jsonschema import make_validator_for
from schemathesis.core.parameters import LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.generation import GenerationMode
from schemathesis.generation.drivers import CoverageGenerator
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.builder import (
    HypothesisTestConfig,
    HypothesisTestMode,
    create_test,
)
from schemathesis.generation.meta import CoverageScenario, TestPhase
from schemathesis.resources import PoolDraw, PoolPick
from schemathesis.specs.openapi.checks import negative_data_rejection
from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases
from schemathesis.specs.openapi.coverage._schema import (
    CoverageContext,
    HashSet,
    _negative_format,
    cover_schema_iter,
    quote_path_parameter,
)
from schemathesis.transport.prepare import prepare_request
from test.utils import assert_requests_call, to_float32


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
    {"query": {"q1": "AAA", "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
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
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": "AAA", "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": 0}},
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
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": "AAA"}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": None}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": False}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": {"j-prop": ANY}},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": [None, None]},
    {"query": {"q1": ANY, "q2": "0"}, "headers": {"h1": ANY, "h2": Pattern("-?[0-9]+")}, "body": "AAA"},
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
    {"query": {"q1": "AAA", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
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
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "AAA", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "null", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "false", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": ANY, "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "4", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": False},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": Pattern(".+")}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": Pattern(".+")}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": {}}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": [None, None]}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": "AAA"}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": None}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": False}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": ANY}},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": [None, None]},
    {"query": {"q1": "5", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": "AAA"},
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


def load_schema(ctx, parameters=None, request_body=None, responses=None, version="3.0.2", path="/foo", method="post"):
    return schemathesis.openapi.from_dict(
        build_schema(
            ctx,
            parameters=parameters,
            request_body=request_body,
            responses=responses,
            version=version,
            path=path,
            method=method,
        )
    )


def assert_positive_coverage(schema, expected, path=None):
    return assert_coverage(schema, [GenerationMode.POSITIVE], expected, path)


def assert_negative_coverage(schema, expected, path=None):
    return assert_coverage(schema, [GenerationMode.NEGATIVE], expected, path)


ALL_MODES = list(GenerationMode)


def run_test(operation, test, modes=ALL_MODES, generate_duplicate_query_parameters=None, unexpected_methods=None):
    # Mutate the operation's schema config directly: `iter_coverage_cases` reads phase
    # settings off `self.config`, so a separate `ProjectConfig` would never reach it.
    config = operation.schema.config
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
    loaded = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {"application/json": {"schema": body_schema}},
        },
        version=version,
    )
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


def _iter_cases(operation, *generation_modes, generation_config=None):
    return list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=list(generation_modes),
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=generation_config or operation.schema.config.generation,
        )
    )


def _generate_cases(operation, generation_mode, *, project_config=None, generation_config=None):
    coverage_config = operation.schema.config.phases.coverage
    if project_config is not None:
        coverage_config.generate_duplicate_query_parameters = (
            project_config.phases.coverage.generate_duplicate_query_parameters
        )
        coverage_config.unexpected_methods = project_config.phases.coverage.unexpected_methods
        generation_config = generation_config or project_config.generation
    else:
        coverage_config.generate_duplicate_query_parameters = False
        coverage_config.unexpected_methods = set()
        generation_config = generation_config or operation.schema.config.generation
    return list(
        CoverageGenerator(
            operation=operation,
            generation_modes=[generation_mode],
            auth_storage=None,
            as_strategy_kwargs={},
            feedback=FeedbackSources(),
            generation_config=generation_config,
        )
    )


def _optimized_body_schema(operation, media_type="application/json"):
    return next(alt.optimized_schema for alt in operation.body if alt.media_type == media_type)


def _body_validator(operation, media_type="application/json", *, validate_formats=True):
    return jsonschema_rs.validator_for(_optimized_body_schema(operation, media_type), validate_formats=validate_formats)


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
                "query": {"key": "AAA"},
            },
            {
                "query": {"key": "null"},
            },
            {
                "query": {"key": "false"},
            },
        ],
    )


def test_negative_type_violations_for_enum_property_under_allof(ctx):
    # `allOf` canonicalisation drops `type` from `{type, enum}` properties; the engine must
    # still emit type-violation negatives for those properties in mixed-mode coverage.
    schema = build_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
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
                },
            },
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
            {"body": {"color": {}}},
            {"body": {"color": [None, None]}},
            {"body": {"color": None}},
            {"body": {"color": False}},
            {"body": {"color": 0}},
            {"body": {"color": "AAA"}},
            {"body": {"color": "blue"}},
            {"body": {"color": "red"}},
        ],
    )


def test_negative_per_property_emitted_when_inflated_template_unsatisfiable(ctx):
    # One unsatisfiable optional property must not silence per-property negatives on the others.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "format": {"enum": ["json", "xml"], "type": "string"},
                                        "unsat": {"type": "integer", "minimum": 10, "maximum": 5},
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
    operation = schema["/foo"]["POST"]
    cases = list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
    )
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
    schema = build_schema(
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
    )
    loaded = schemathesis.openapi.from_dict(schema)
    loaded.config.phases.coverage.generate_duplicate_query_parameters = False
    operation = loaded["/foo"]["get"]
    config = operation.schema.config
    config.generation.update(modes=[GenerationMode.POSITIVE])
    config.phases.examples.enabled = False
    config.phases.fuzzing.enabled = False
    config.phases.stateful.enabled = False
    values = []

    def collect(case):
        if case.meta.phase.name != TestPhase.COVERAGE:
            return
        if case.meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        values.append(case.query.get("domain"))

    test_func = create_test(
        operation=operation,
        test_func=collect,
        config=HypothesisTestConfig(
            modes=[HypothesisTestMode.COVERAGE],
            project=config,
            settings=settings(phases=[Phase.explicit]),
        ),
    )
    test_func()

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
        request_body={
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["color"],
                        "properties": {
                            "color": {"type": "string", "enum": ["red", "blue"]},
                        },
                    },
                },
            },
        },
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
    raw = ctx.openapi.build_schema(
        {
            "/foo": {
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
                                            "items": {"$ref": "#/components/schemas/Item"},
                                            "minItems": 1,
                                            "maxItems": 50,
                                            "examples": [[{"id": "a"}]],
                                        },
                                    },
                                },
                            },
                        },
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
        components={
            "schemas": {
                "Item": {"type": "object", "properties": {"id": {"type": "string"}}},
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(raw)
    operation = loaded["/foo"]["POST"]
    cases = list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
    )
    empty_array = [c for c in cases if isinstance(c.body, dict) and c.body.get("items") == []]
    assert empty_array and all(
        c.meta.phase.data.scenario == CoverageScenario.ARRAY_BELOW_MIN_ITEMS for c in empty_array
    ), [c.body for c in cases]


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
                "query": {"key": "AAA"},
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
                "headers": {"X-API-Key-1": "AAA"},
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
    schema = build_schema(ctx, request_body={"required": True, "content": {"application/json": {"schema": schema}}})
    assert_negative_coverage(schema, expected)


def test_string_with_format(ctx):
    schema = load_schema(
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
                    "q1": [],
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
                "http://127.0.0.1/foo?q=AAA",
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
                "http://127.0.0.1/foo?q=AAA",
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
                "http://127.0.0.1/foo?q=AAA",
                "http://127.0.0.1/foo?q=null",
                "http://127.0.0.1/foo?q=false",
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


@pytest.mark.parametrize("location", [ParameterLocation.QUERY, ParameterLocation.PATH])
def test_negative_boolean_not_coercible_wire_value(ctx_factory, location):
    # Lenient parsers coerce 0/1/true/false to booleans, so those wire values are not type violations for a boolean parameter
    nctx = ctx_factory(location=location, generation_modes=[GenerationMode.NEGATIVE])
    schema = {"type": "boolean", "default": False}
    values = [
        generated.value
        for generated in cover_schema_iter(nctx, schema, HashSet())
        if generated.scenario == CoverageScenario.INCORRECT_TYPE
    ]

    coercible = {"0", "1", "true", "false"}
    rendered = {str(value).lower() for value in values}
    assert not (rendered & coercible), f"Boolean-coercible negatives generated: {values}"


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
    schema = ctx.openapi.load_schema(
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

    operation = schema["/items"]["post"]

    def test(case):
        # Body is `required`, hence should never be unset for positive tests
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


@pytest.mark.parametrize("required", [["name"], ["name", "description"]])
def test_request_body_with_references(ctx, required):
    schema = ctx.openapi.load_schema(
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

    operation = schema["/items"]["post"]

    def test(case):
        # Body is `required`, hence should never be unset for positive tests
        assert case.body is not NOT_SET, case.meta.phase.data.description

    run_positive_test(operation, test)


def test_request_body_without_validation_keywords(ctx):
    schema = ctx.openapi.load_schema(
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

    operation = schema["/items"]["post"]

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


def test_nested_parameters(ctx):
    schema = ctx.openapi.load_schema(
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
    schema = ctx.openapi.load_schema({"/test": {"post": operation}}, components=components)
    for operation in schema.get_all_operations():
        if isinstance(operation, Ok):
            for _ in iter_coverage_cases(
                operation=operation.ok(),
                generation_modes=list(GenerationMode),
                generate_duplicate_query_parameters=False,
                unexpected_methods=set(),
                generation_config=schema.config.generation,
            ):
                pass
        else:
            assert "Unresolvable reference in the schema" in str(operation.err())


def test_urlencoded_array_body_is_serializable(ctx):
    # Form-urlencoded bodies declared as top-level arrays used to abort the operation when prepared.
    schema = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "integer"}},
                            "required": ["id"],
                        },
                    },
                }
            },
        },
    )
    operation = schema["/foo"]["post"]
    config = SanitizationConfig(enabled=False)
    count = 0
    for case in iter_coverage_cases(
        operation=operation,
        generation_modes=list(GenerationMode),
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=operation.schema.config.generation,
    ):
        prepare_request(case, headers=None, config=config)
        count += 1
    assert count > 0


def test_urlencoded_payloads_are_valid(ctx):
    schema = load_schema(
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

    operation = schema["/foo"]["post"]

    def test(case):
        if case.meta.phase != TestPhase.COVERAGE:
            return
        assert_requests_call(case)

    run_test(operation, test)


def test_malformed_content_type(ctx):
    schema = load_schema(
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

    operation = schema["/foo"]["post"]

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


def test_binary_format_should_not_generate_empty_string_as_invalid(ctx, cli, snapshot_cli):
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
    loaded = ctx.openapi.load_schema(
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
    loaded = load_schema(
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


def test_additional_properties_without_type_positive(ctx):
    # Azure swagger 2.0 schemas commonly omit `type: object` on tag maps; the implicit object
    # must still get a positive case satisfying `additionalProperties` so coverage flips `valid`.
    loaded = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "properties": {
                            "tags": {
                                "additionalProperties": {"type": "string"},
                            }
                        },
                    }
                }
            },
        },
    )
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_positive_test(operation, collect)

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
    loaded = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
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
                    }
                }
            },
        },
    )
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_positive_test(operation, collect)

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
    loaded = load_schema(
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
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "required": ["thing"],
                                "properties": {"thing": {"$ref": "#/definitions/Loose", "type": "object"}},
                            },
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
        definitions={"Loose": {"properties": {"x": {"type": "string"}}, "required": ["x"]}},
    )
    operation = schema["/foo"]["POST"]
    validator = operation.schema.adapter.jsonschema_validator_cls(_optimized_body_schema(operation))

    false_negatives = [
        case.body
        for case in _iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY and validator.is_valid(case.body)
    ]
    assert false_negatives == []


def test_negative_required_drops_false_negatives_at_body_root_with_ref_sibling(ctx):
    # Body root is `$ref` + sibling `required: [...]`. Draft 4 ignores siblings of `$ref`,
    # so the validator only enforces the bare ref target — which has no matching `required`.
    # Removing the listed required field passes the target vacuously and must not be emitted.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {"$ref": "#/definitions/Wrapper", "required": ["location"]},
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
        # Second definition forces bundling — single-def schemas get inlined and lose
        # the `$ref` + sibling shape that triggers the bug.
        definitions={
            "Wrapper": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "sku": {"$ref": "#/definitions/Sku"},
                },
            },
            "Sku": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    )
    operation = schema["/foo"]["POST"]
    validator = operation.schema.adapter.jsonschema_validator_cls(_optimized_body_schema(operation))

    false_negatives = [
        case.body
        for case in _iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY and validator.is_valid(case.body)
    ]
    assert false_negatives == []


def test_negative_ref_sibling_with_binary_format_does_not_crash_validator(ctx):
    # `$ref` + sibling triggers the unmerged-validator path; the merged target produces
    # values containing Binary, which jsonschema_rs cannot validate and raises ValueError.
    schema = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Upload",
                                    "required": ["file"],
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
                "Upload": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "sku": {"$ref": "#/components/schemas/Sku"},
                    },
                },
                # Second component forces bundling so the `$ref` + sibling shape survives
                # into `cover_schema_iter` instead of being inlined.
                "Sku": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
    )
    operation = schema["/upload"]["POST"]

    cases = _iter_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0


def test_positive_body_generated_for_object_with_metadata_and_unsatisfiable_optionals(ctx):
    # Object schema with metadata keyword (`title`) plus optional properties that are
    # unsatisfiable (`{"not": {}}` from readOnly). Empty `{}` is a valid positive body;
    # the generator must produce at least one rather than falling back on a negative body.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "title": "Resource",
                                "type": "object",
                                "properties": {
                                    "id": {"not": {}},
                                    "created_at": {"not": {}},
                                    "name": {"type": "string"},
                                },
                            },
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/foo"]["POST"]

    positive_bodies = [
        case.body
        for case in _iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]
    assert positive_bodies, "Expected at least one positive body case"
    assert all(isinstance(body, dict) for body in positive_bodies), (
        f"Positive bodies must be objects per `type: object`; got: {positive_bodies}"
    )


def test_positive_body_generated_when_required_excludes_forbidden_properties(ctx):
    # A `readOnly` field listed in `required` must not block positive body generation.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "allOf": [{"type": "object"}],
                                "properties": {
                                    "id": {"type": "string", "readOnly": True},
                                    "name": {"type": "string"},
                                },
                                "required": ["id", "name"],
                            },
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/foo"]["POST"]
    positive_bodies = [
        case.body
        for case in _iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY
    ]
    assert positive_bodies, "Expected at least one positive body case"
    assert all("id" not in body for body in positive_bodies), (
        f"Positive bodies must not contain forbidden `id`; got: {positive_bodies}"
    )


def test_parameter_positive_coverage_when_body_fallback_negative(ctx):
    # An unsatisfiable body must not suppress positive coverage of unrelated parameters.
    schema = ctx.openapi.load_schema(
        {
            "/push": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "format",
                            "schema": {"type": "string", "enum": ["json", "jsonp", "msgpack", "html"]},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {"type": "object", "properties": {"channel": {"type": "string"}}},
                                        {"type": "object", "properties": {"channel": {"type": "string"}}},
                                    ]
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/push"]["POST"]
    assert {
        case.query.get("format")
        for case in iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
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
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {"in": "header", "name": "X-Token", "required": True, "type": "string"},
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "allOf": [{"type": "object"}],
                                "properties": {"id": {"readOnly": True, "type": "string"}},
                            },
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/foo"]["POST"]

    bad = []
    for case in _iter_cases(operation, GenerationMode.NEGATIVE):
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY:
            continue
        body_component = case.meta.components.get(ParameterLocation.BODY)
        if body_component is not None and body_component.mode == GenerationMode.NEGATIVE:
            bad.append((case.meta.phase.data.description, case.body))
    assert bad == []


def test_positive_number_near_boundary_respects_multiple_of(ctx):
    # IEEE-754 subtraction `maximum - multipleOf` drifts (e.g. `99999.99 - 0.01 = 99999.98000000001`).
    # The validator rejects the drifted value as not a multiple. Decimal-based arithmetic stays exact.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "amount": {"type": "number", "minimum": 0, "maximum": 99999.99, "multipleOf": 0.01}
                                },
                            },
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/foo"]["POST"]
    validator = operation.schema.adapter.jsonschema_validator_cls(_optimized_body_schema(operation))

    invalid = [
        case.body
        for case in _iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY and not validator.is_valid(case.body)
    ]
    assert invalid == []


def test_positive_number_boundary_respects_exclusive_bounds(ctx):
    # Boolean `exclusiveMinimum: true` + `exclusiveMaximum: true` combined with `minimum: 0`
    # / `maximum: 1` (legacy OpenAPI 3.0 form). The boundary generator's `+= 1` / `-= 1`
    # adjustments overshoot the other exclusive boundary; emitted values must validate.
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
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
                        }
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/foo"]["POST"]
    validator = operation.schema.adapter.jsonschema_validator_cls(_optimized_body_schema(operation))

    invalid = [
        case.body
        for case in _iter_cases(operation, GenerationMode.POSITIVE)
        if case.meta.phase.data.parameter_location == ParameterLocation.BODY and not validator.is_valid(case.body)
    ]
    assert invalid == []


def test_additional_properties_anyof_positive(ctx):
    loaded = load_schema(
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
    empty = [c for c in cases if c.meta.phase.data.scenario == CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES]
    assert any(c.body == {} for c in empty), (
        f"Should generate empty object for minProperties: 1 alongside additionalProperties. Got: {[c.body for c in cases]}"
    )


def test_anyof_with_outer_properties_yields_branch_constrained_bodies(ctx):
    # Outer property `status: string` is tightened by each anyOf branch via enum;
    # positive bodies must satisfy at least one branch's enum.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {"status": {"type": "string"}},
                                        "anyOf": [
                                            {"properties": {"status": {"enum": ["succeeded"]}}},
                                            {"properties": {"status": {"enum": ["failed", "rejected"]}}},
                                        ],
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases = _generate_cases(operation, GenerationMode.POSITIVE)
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
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "oneOf": [
                                            {"properties": {"a": {"type": "integer"}}},
                                            {"properties": {"b": {"type": "integer"}}},
                                        ],
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases = _generate_cases(operation, GenerationMode.POSITIVE)
    assert not any(c.body == {} for c in cases), (
        f"Empty `{{}}` matches both oneOf branches and must not be yielded. Got: {[c.body for c in cases]}"
    )


def test_anyof_discriminator_branch_required_propagated(ctx):
    # anyOf branches discriminated by a `type` enum. The branch with type=A also requires
    # `priority`. A positive body claiming type=A must include priority.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
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
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases = _generate_cases(operation, GenerationMode.POSITIVE)
    bad = [c.body for c in cases if isinstance(c.body, dict) and c.body.get("type") == "A" and "priority" not in c.body]
    assert not bad, f"Positive body for branch type=A must include branch-required `priority`. Got: {bad}"


def test_request_body_example_invalid_against_schema_not_yielded(ctx):
    # Boolean `exclusiveMinimum` (Draft 4) defeats Draft-2020-12 auto-detection; the example
    # missing `riskFreeRate` must still be filtered out as a positive case.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
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
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases = _generate_cases(operation, GenerationMode.POSITIVE)
    bad = [c.body for c in cases if isinstance(c.body, dict) and "riskFreeRate" not in c.body]
    assert not bad, f"Spec example invalid against schema must not be yielded. Got: {bad}"


def test_required_outside_allof_propagated_into_canonicalised_branches(ctx):
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "components": {
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
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "schedule": {"$ref": "#/components/schemas/Wrapper"},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases: list = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_positive_test(operation, collect)

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
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "components": {
                "schemas": {
                    "Base": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"etag": {"type": "string"}},
                    }
                }
            },
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "allOf": [{"$ref": "#/components/schemas/Base"}],
                                        "properties": {"properties": {"properties": {"x": {"type": "string"}}}},
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    validator = make_validator_for(operation.body[0].optimized_schema)
    cases: list = []

    def collect(case):
        if (
            case.meta.phase.name == TestPhase.COVERAGE
            and case.meta.components[ParameterLocation.BODY].mode == GenerationMode.POSITIVE
        ):
            cases.append(case)

    run_positive_test(operation, collect)

    invalid = [c.body for c in cases if not validator.is_valid(c.body)]
    assert not invalid, f"Positive coverage produced bodies invalid per the strict schema: {invalid}"


def test_positive_body_under_unsatisfiable_allof_chain(ctx):
    # Outer's `required` key is absent from a base with `additionalProperties: false`,
    # so the strict canonical schema is unsatisfiable.
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "components": {
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
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"payload": {"$ref": "#/components/schemas/Wrapper"}},
                                        "required": ["payload"],
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    validator = make_validator_for(operation.body[0].optimized_schema)
    cases: list = []

    def collect(case):
        if (
            case.meta.phase.name == TestPhase.COVERAGE
            and case.meta.components[ParameterLocation.BODY].mode == GenerationMode.POSITIVE
        ):
            cases.append(case)

    run_positive_test(operation, collect)

    invalid = [c.body for c in cases if not validator.is_valid(c.body)]
    assert not invalid, f"Positive coverage produced bodies invalid per the strict schema: {invalid}"


def test_positive_body_with_sibling_oneof_required_via_ref(ctx):
    # Sibling `oneOf: [{required: [a]}, {required: [b]}]` makes a and b mutually exclusive;
    # the combinator filter needs the root bundle attached to resolve sub-refs and apply it.
    schema = ctx.openapi.load_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"inner": {"$ref": "#/components/schemas/Inner"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
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
    operation = schema["/x"]["POST"]
    validator = make_validator_for(operation.body[0].optimized_schema)
    cases: list = []

    def collect(case):
        if (
            case.meta.phase.name == TestPhase.COVERAGE
            and case.meta.components[ParameterLocation.BODY].mode == GenerationMode.POSITIVE
        ):
            cases.append(case)

    run_positive_test(operation, collect)

    invalid = [c.body for c in cases if not validator.is_valid(c.body)]
    assert not invalid, f"Positive coverage produced bodies invalid per the strict schema: {invalid}"


def test_ref_with_type_sibling_dropped_in_openapi_3_0(ctx):
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "components": {
                "schemas": {
                    "Inner": {
                        "type": "object",
                        "properties": {"foo": {"type": "string"}},
                        "required": ["foo"],
                    },
                }
            },
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "Field": {
                                                "$ref": "#/components/schemas/Inner",
                                                "type": "string",
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases: list = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_positive_test(operation, collect)

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
        for c in cases
        if c.meta.phase.data.scenario == CoverageScenario.OBJECT_ADDITIONAL_PROPERTY
        and isinstance(c.body, dict)
        and len(c.body) > 1
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
    below = [c for c in cases if c.meta.phase.data.scenario == CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES]
    assert len(below) == 0, (
        f"Should NOT generate OBJECT_BELOW_MIN_PROPERTIES when required > minProperties. Got: {below}"
    )


def test_missing_content_type_header(ctx):
    # Regression: "missing Content-Type header" test case should not include Content-Type in request
    loaded = load_schema(
        ctx,
        parameters=[
            {"in": "header", "name": "Content-Type", "schema": {"type": "string"}, "required": True},
        ],
        request_body={
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
    )
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


def test_path_template_with_dot_prefixed_placeholder(ctx):
    # RFC 6570 label expansion (`{.format}`) appears in real schemas; coverage used to abort the operation.
    loaded = load_schema(
        ctx,
        path="/projects/{id}{.format}",
        method="get",
        parameters=[
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": ".format", "in": "path", "required": True, "schema": {"type": "string", "enum": ["json"]}},
        ],
    )
    operation = loaded["/projects/{id}{.format}"]["get"]
    config = SanitizationConfig(enabled=False)
    paths = set()
    for case in iter_coverage_cases(
        operation=operation,
        generation_modes=list(GenerationMode),
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=operation.schema.config.generation,
    ):
        prepared = prepare_request(case, headers=None, config=config)
        paths.add(prepared.url)
    assert paths


def test_path_parameter_with_slash_in_custom_format(ctx):
    # See GH-3527
    schemathesis.openapi.format("ipv4-network", st.sampled_from(["0.0.0.0/0"]))
    loaded = load_schema(
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
    operation = loaded["/blocks/{block}"]["get"]

    path_values = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            path_values.append(case.path_parameters.get("block"))

    run_positive_test(operation, collect)

    assert path_values, "No coverage cases generated"
    assert all(v == "0.0.0.0%2F0" for v in path_values), f"Unexpected values: {path_values}"


def _collect_xml_coverage_cases(ctx, body_schema, *, positive=False, full_schema=None):
    """Build an XML schema, run coverage, and return coverage phase cases."""
    if full_schema is not None:
        loaded = schemathesis.openapi.from_dict(full_schema)
    else:
        loaded = load_schema(
            ctx,
            request_body={
                "required": True,
                "content": {"application/xml": {"schema": body_schema}},
            },
        )
    operation = loaded["/foo"]["post"]

    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    if positive:
        run_positive_test(operation, collect)
    else:
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


def test_xml_string_leaf_has_non_empty_positive_case(ctx):
    # Empty XML elements bypass server-side string-keyword validators on common parsers.
    cases = _collect_xml_coverage_cases(
        ctx,
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        positive=True,
    )
    populated = [
        c.body for c in cases if isinstance(c.body, dict) and isinstance(c.body.get("name"), str) and c.body["name"]
    ]
    assert populated, f"Expected at least one positive case with a non-empty 'name'; got: {[c.body for c in cases]}"


def test_xml_optional_ref_object_property_populated_in_positive_cases(ctx):
    raw = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/xml": {"schema": {"$ref": "#/components/schemas/Wrapper"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
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
    cases = _collect_xml_coverage_cases(ctx, None, positive=True, full_schema=raw)
    with_child = [
        c.body
        for c in cases
        if isinstance(c.body, dict) and isinstance(c.body.get("child"), dict) and "value" in c.body["child"]
    ]
    assert with_child, (
        f"Expected at least one positive case populating optional 'child'; got: {[c.body for c in cases]}"
    )


def test_query_method_appears_in_unspecified_methods(ctx):
    schema = ctx.openapi.load_schema(
        {"/search": {"post": {"responses": {"200": {"description": "OK"}}}}},
        version="3.2.0",
    )
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
    loaded = load_schema(
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
    monkeypatch.setattr(
        "schemathesis.specs.openapi.coverage._schema.from_schema", lambda *_args, **_kwargs: st.just("XN--9krT00a")
    )
    ctx = CoverageContext(
        root_schema={"type": "string", "format": "hostname"},
        location=ParameterLocation.QUERY,
        media_type=None,
        generation_modes=[GenerationMode.NEGATIVE],
        is_required=True,
        custom_formats={},
        validator_cls=validator_cls,
    )

    generator = _negative_format(ctx, {"type": "string", "format": "hostname"}, "hostname")

    if should_generate:
        value = next(generator)
        assert value.value == "XN--9krT00a"
    else:
        with pytest.raises(Unsatisfiable):
            next(generator)


def test_negative_format_serves_cached_value(nctx):
    # Random strings almost never look like IPv4, so the violation filter accepts and the
    # strategy returns immediately. The second call must yield the same value, served from cache.
    schema = {"type": "string", "format": "ipv4"}
    assert next(_negative_format(nctx, schema, "ipv4")).value == next(_negative_format(nctx, schema, "ipv4")).value


def test_negative_format_serves_cached_unsatisfiable(nctx):
    # Lowercase-letter strings are valid single-label hostnames, so the violation filter
    # rejects every draw. The second call must raise from the cached sentinel.
    schema = {"type": "string", "format": "hostname", "pattern": "^[a-z]+$"}
    with pytest.raises(Unsatisfiable):
        next(_negative_format(nctx, schema, "hostname"))
    with pytest.raises(Unsatisfiable):
        next(_negative_format(nctx, schema, "hostname"))


@pytest.mark.parametrize(
    ("types", "expected_kind"),
    [(["string", "number", "null"], (int, float)), (["string", "integer", "null"], int)],
    ids=["number", "integer"],
)
def test_multi_type_union_yields_numeric_branch(types, expected_kind):
    # Numeric branch of a multi-type union must produce a numeric value, not a string drawn from a sibling branch.
    ctx = CoverageContext(
        root_schema={},
        location=ParameterLocation.QUERY,
        media_type=None,
        generation_modes=[GenerationMode.POSITIVE],
        is_required=False,
        custom_formats={},
        validator_cls=jsonschema_rs.validator_for({}).__class__,
    )
    values = [v.value for v in cover_schema_iter(ctx, {"type": types})]
    assert any(isinstance(v, expected_kind) and not isinstance(v, bool) for v in values), values


@pytest.mark.parametrize(
    ("keyword", "bound"),
    [
        ("exclusiveMinimum", 0),
        ("exclusiveMinimum", 1.0),
        ("exclusiveMaximum", 1.0),
        ("exclusiveMinimum", 0.1),
        ("exclusiveMaximum", 16777217),
    ],
    ids=["min-zero", "min-representable", "max-representable", "min-rounds-up", "max-rounds-down"],
)
def test_float_format_boundary_strictly_satisfies_bound(pctx, keyword, bound):
    # The emitted boundary value must still satisfy the exclusive bound after a server narrows it to float32.
    schema = {"type": "number", "format": "float", keyword: bound}
    values = [v.value for v in cover_schema_iter(pctx, schema, HashSet())]
    assert values, schema
    for value in values:
        narrowed = to_float32(float(value))
        if keyword == "exclusiveMinimum":
            assert narrowed > bound, (value, narrowed)
        else:
            assert narrowed < bound, (value, narrowed)


@pytest.mark.parametrize("bound", [1e39, 10**1000], ids=["float", "integer"])
def test_float_format_bound_outside_single_precision_range_does_not_crash(pctx, bound):
    schema = {"type": "number", "format": "float", "exclusiveMaximum": bound}
    values = [v.value for v in cover_schema_iter(pctx, schema, HashSet())]
    for value in values:
        assert to_float32(float(value)) < 1e39, value


def test_float_format_unsatisfiable_bound_emits_nothing(pctx):
    # No finite float32 exceeds 10**1000, so there is no representable positive value to emit.
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 10**1000}
    assert [v.value for v in cover_schema_iter(pctx, schema, HashSet())] == []


@pytest.mark.parametrize("key", ["example", "examples", "default"])
def test_float_format_collapsing_example_not_emitted(pctx, key):
    # A user value valid as float64 but collapsing to 0 in float32 must not be emitted as positive.
    value = [5e-324] if key == "examples" else 5e-324
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 0, key: value}
    values = [v.value for v in cover_schema_iter(pctx, schema, HashSet())]
    assert values and all(to_float32(float(v)) > 0 for v in values), values


def test_float_format_representable_example_still_emitted(pctx):
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 0, "example": 1000}
    assert 1000 in [v.value for v in cover_schema_iter(pctx, schema, HashSet())]


@pytest.mark.parametrize(
    "modes",
    [[GenerationMode.POSITIVE], [GenerationMode.POSITIVE, GenerationMode.NEGATIVE]],
    ids=["positive", "mixed"],
)
def test_unsatisfiable_required_param_emits_no_positive_case(ctx, modes):
    # An unsatisfiable required parameter leaves no valid positive request, even when mixed mode seeds the
    # template with a negative value.
    schema = ctx.openapi.load_schema(
        {
            "/route": {
                "get": {
                    "parameters": [
                        {
                            "name": "f",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/route"]["GET"]
    cases = _iter_cases(operation, *modes)
    positive = [case.query for case in cases if case.meta.generation.mode == GenerationMode.POSITIVE]
    assert positive == [], positive


def test_unsatisfiable_required_path_param_emits_no_positive_case(ctx):
    # A required path parameter falls back to a negative sample when nothing is representable; the positive
    # default case must still be suppressed rather than shipping that sample as positive.
    schema = ctx.openapi.load_schema(
        {
            "/items/{f}": {
                "get": {
                    "parameters": [
                        {
                            "name": "f",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/items/{f}"]["GET"]
    cases = _iter_cases(operation, GenerationMode.POSITIVE)
    assert cases == [], [case.path_parameters for case in cases]


def test_unsatisfiable_required_param_suppresses_positive_from_other_params(ctx):
    # A second, satisfiable parameter must not produce any positive case while a sibling required
    # parameter is unsatisfiable: the whole operation has no valid positive request.
    schema = ctx.openapi.load_schema(
        {
            "/route": {
                "get": {
                    "parameters": [
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
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/route"]["GET"]
    cases = _iter_cases(operation, GenerationMode.POSITIVE)
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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/test"]["post"]
    validator = operation.schema.adapter.jsonschema_validator_cls(body_schema, validate_formats=False)

    missing_header_cases = [
        case
        for case in _iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.scenario == CoverageScenario.MISSING_PARAMETER
        and case.meta.phase.data.parameter == "X-Required-Header"
    ]

    assert missing_header_cases, "Expected at least one MISSING_PARAMETER case for X-Required-Header"
    # Template body must be valid so the server reaches header validation, not body rejection.
    assert all(validator.is_valid(case.body) for case in missing_header_cases), (
        f"Missing-header cases must have a valid body, got: {[case.body for case in missing_header_cases]}"
    )


def test_missing_required_header_case_respects_before_call_hook_restoring_header(ctx):
    schema = ctx.openapi.load_schema(
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
    operation = schema["/items"]["put"]

    missing_header_case = next(
        case
        for case in _iter_cases(operation, GenerationMode.NEGATIVE)
        if case.meta.phase.data.scenario == CoverageScenario.MISSING_PARAMETER
        and case.meta.phase.data.parameter == "X-Required-Header"
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

    # Verify some cases are produced without hook
    config = ProjectConfig()
    base_cases = _generate_cases(operation, GenerationMode.POSITIVE, project_config=config)
    assert base_cases, "Expected coverage cases before filtering"

    @loaded.hook
    def filter_case(context, case):
        return False  # reject everything

    filtered_cases = _generate_cases(operation, GenerationMode.POSITIVE, project_config=config)
    assert filtered_cases == [], "filter_case hook should suppress all coverage cases"


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

    config = ProjectConfig()
    operation = loaded["/foo"]["get"]
    cases = _generate_cases(operation, GenerationMode.POSITIVE, project_config=config)

    assert cases, "Expected at least one coverage case"
    assert all(c.query is None or c.query.get("injected") == "yes" for c in cases), (
        "map_case hook should have injected 'injected' into every query"
    )


def test_content_json_query_params_single_encoding_in_coverage(ctx):
    # See GH-3701
    loaded = load_schema(
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
    config = ProjectConfig()
    operation = loaded["/foo"]["post"]

    cases = _generate_cases(operation, GenerationMode.POSITIVE, project_config=config)

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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/hooks"]["POST"]

    cases = _iter_cases(operation, GenerationMode.POSITIVE)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/zipcode"]["post"]

    optimized_schema = _optimized_body_schema(operation)
    assert "maxLength" in optimized_schema, f"maxLength must be preserved in optimized_schema; got: {optimized_schema}"

    validator = jsonschema_rs.validator_for(optimized_schema, validate_formats=True)
    max_length_cases = [
        case
        for case in _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/pin"]["post"]

    validator = _body_validator(operation)

    positive_cases = _iter_cases(operation, GenerationMode.POSITIVE, generation_config=loaded.config.generation)
    for case in positive_cases:
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid per optimized_schema: {case.body!r}"


def test_coverage_positive_allof_ref_property_merge(ctx):
    # Multi-level allOf chain (Child -> Intermediate -> Base) where Base defines 'location'.
    # canonicalish leaves an unresolved $ref inside the merged schema; cover_schema_iter must
    # deep-merge 'properties' from the resolved ref, not overwrite, so 'location' stays present.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/resources/{name}"]["put"]

    validator = _body_validator(operation)

    positive_cases = _iter_cases(operation, GenerationMode.POSITIVE, generation_config=loaded.config.generation)
    for case in positive_cases:
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_body_with_boolean_property_key_negative(ctx):
    # YAML parses bare `on:` as boolean True, so schemas loaded from YAML can have bool keys in `properties`.
    schema = ctx.openapi.load_schema(
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
    operation = schema["/hooks"]["POST"]

    cases = _iter_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0


def test_coverage_form_urlencoded_binary_format_negative(ctx):
    schema = ctx.openapi.load_schema(
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
    operation = schema["/upload"]["POST"]

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        assert case.meta.phase.name == TestPhase.COVERAGE


def test_coverage_negative_empty_dict_additional_properties_not_treated_as_false(ctx):
    # `additionalProperties: {}` is equivalent to `true` — any extra property is valid.
    schema = ctx.openapi.load_schema(
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
    operation = schema["/search"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), (
            f"NEGATIVE body must be schema-invalid, got schema-valid body: {case.body!r}"
        )


def test_coverage_negative_pattern_with_control_chars_uses_schema_validator(ctx):
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
    operation = schema["/items"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/docs"]["post"]
    validator = _body_validator(operation)

    cases = _generate_cases(operation, GenerationMode.POSITIVE)
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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/users"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.POSITIVE)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/register"]["post"]

    validator = _body_validator(operation)

    positive_cases = _iter_cases(operation, GenerationMode.POSITIVE, generation_config=loaded.config.generation)
    for case in positive_cases:
        assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid per optimized_schema: {case.body!r}"


def _positive_body_context():
    return CoverageContext(
        root_schema={},
        location=ParameterLocation.BODY,
        media_type=("application", "json"),
        generation_modes=[GenerationMode.POSITIVE],
        is_required=True,
        custom_formats={},
        validator_cls=jsonschema_rs.validator_for({}).__class__,
    )


@pytest.mark.parametrize(
    "items_hint",
    [{"example": {"id": "X"}}, {"examples": [{"id": "X"}]}, {"default": {"id": "X"}}],
    ids=["example", "examples", "default"],
)
def test_array_items_spec_hint_seeds_generated_array(items_hint):
    # Array elements draw from `items`-level spec hints.
    items = {"type": "object", "properties": {"id": {"type": "string"}}, **items_hint}
    assert _positive_body_context().generate_from_schema({"type": "array", "items": items, "minItems": 1}) == [
        {"id": "X"}
    ]


@pytest.mark.parametrize(
    "hint_extra",
    [
        {"example": {"id": "X", "ro": "v"}},
        {"examples": [{"id": "X", "ro": "v"}]},
        {"default": {"id": "X", "ro": "v"}},
    ],
    ids=["example", "examples", "default"],
)
def test_spec_hint_recovers_after_dropping_readonly_stripped_keys(hint_extra):
    # Examples carrying `readOnly` keys (forbidden in request schemas) must still seed generation after dropping them.
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "ro": {"not": {}}},
        **hint_extra,
    }
    assert _positive_body_context().generate_from_schema(schema) == {"id": "X"}


def test_example_with_nested_ref_violation_is_not_used(ctx):
    # An `example` whose nested values violate an enum reachable via `$ref` must not
    # be emitted as a positive case. Without bundle-aware validation the ref cannot
    # resolve, the validator silently accepts the example, and an invalid body ships.
    raw = ctx.openapi.build_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Wrapper"}}},
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
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
    loaded = schemathesis.openapi.from_dict(raw)
    operation = loaded["/r"]["POST"]
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
    cases = list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
    )
    assert cases, "expected at least one positive coverage case"
    for case in cases:
        assert validator.is_valid(case.body), f"Invalid positive body emitted: {case.body!r}"


def test_content_example_invalid_under_draft4_only_schema_is_not_used(ctx):
    # Schemas mixing draft-specific keywords with content-level examples must not ship examples
    # whose values violate item-schemas (e.g. `null` in a `number` array) as positive coverage bodies.
    raw = ctx.openapi.build_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
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
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
    )
    loaded = schemathesis.openapi.from_dict(raw)
    operation = loaded["/r"]["POST"]
    cases = list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
    )
    assert cases, "expected at least one positive coverage case"
    for case in cases:
        body = case.body
        if isinstance(body, dict) and isinstance(body.get("w"), list):
            assert None not in body["w"], f"Invalid positive body emitted: {body!r}"


def test_oneof_ref_branches_with_discriminator_each_get_distinct_positive_coverage(ctx):
    # A nested discriminator `oneOf` under an outer `oneOf`-discriminated body must
    # yield at least one value uniquely satisfying each inner branch.
    raw = ctx.openapi.build_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Rule"}}},
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
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
    for case in iter_coverage_cases(
        operation=operation,
        generation_modes=[GenerationMode.POSITIVE],
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=operation.schema.config.generation,
    ):
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/submit"]["post"]

    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), f"NEGATIVE body is schema-valid: {case.body!r}"


def test_negative_enum_emits_entries_with_type_mismatch_for_keyword_coverage(ctx):
    # Positive path skips every entry as `type`-invalid, so only negatives can exercise `enum` here.
    loaded = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "chunk_size": {"enum": [2, 4, 6, 8, 10], "type": "string"},
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
    operation = loaded["/foo"]["POST"]
    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"value": property_schema},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = loaded["/foo"]["POST"]
    validator = _body_validator(operation)
    for case in _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation):
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), (
            f"NEGATIVE body is schema-valid (mutation had no effect): {case.body!r}"
        )


def test_negative_const_emits_value_with_type_mismatch_for_keyword_coverage(ctx):
    # Positive path skips the const value as `type`-invalid, so only the negative can exercise `const` here.
    loaded = ctx.openapi.load_schema(
        version="3.1.0",
        paths={
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "chunk_size": {"const": 42, "type": "string"},
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
    operation = loaded["/foo"]["POST"]
    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
    emitted = {
        c.body["chunk_size"]
        for c in cases
        if isinstance(c.body, dict) and "chunk_size" in c.body and isinstance(c.body["chunk_size"], int)
    }
    assert 42 in emitted, f"Expected const value as a negative; got: {emitted}"


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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items/{id}"]["put"]

    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items/{id}"]["put"]

    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items/{id}"]["patch"]

    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]

    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), f"NEGATIVE body is schema-valid: {case.body!r}"


def test_negative_min_length_emitted_when_pattern_requires_more_than_bound(ctx):
    # When `minLength > 1` AND `pattern` requires more chars than `minLength - 1`,
    # the bounded draw is unsatisfiable; fall back to truncation rather than dropping the negative.
    loaded = ctx.openapi.load_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "minLength": 2,
                                    "pattern": "^[A-Z][A-Za-z0-9-_+]+(?:/[A-Z][A-Za-z0-9-_+]+)*$",
                                    "type": "string",
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = loaded["/foo"]["POST"]
    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
    short_strings = [
        c.body
        for c in cases
        if isinstance(c.body, str)
        and c.meta is not None
        and c.meta.phase.data.scenario == CoverageScenario.STRING_BELOW_MIN_LENGTH
    ]
    assert short_strings, f"Expected a STRING_BELOW_MIN_LENGTH negative; got bodies: {[c.body for c in cases]}"
    for body in short_strings:
        assert len(body) < 2, f"Negative body {body!r} is not shorter than minLength=2"


def test_coverage_negative_string_property_form_urlencoded_not_wire_identical(ctx):
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]

    validator = _body_validator(operation, "application/x-www-form-urlencoded")

    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]

    validator = _body_validator(operation, "application/xml")

    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/modify"]["PATCH"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.POSITIVE)
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid for oneOf: {case.body!r}"


def test_coverage_positive_body_ref_with_pattern_and_length_constraints(ctx):
    # POSITIVE bodies must satisfy the anchored pattern even when the object body uses
    # `additionalProperties: false` alongside `$ref` properties with pattern/length constraints.
    schema = ctx.openapi.load_schema(
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
    operation = schema["/tasks"]["POST"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.POSITIVE)
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_oneof_branch_required_field_missing_from_branch_properties(ctx):
    # POSITIVE bodies must satisfy the full schema when a oneOf branch requires a field
    # that is defined only in the parent schema's properties, not in the branch's own properties.
    schema = ctx.openapi.load_schema(
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
    operation = schema["/runs"]["POST"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.POSITIVE)
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_negative_format_nullable(ctx):
    # INVALID_FORMAT must produce a non-null string when the schema has `type: ["string", "null"]`.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]

    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
    for case in negative_cases:
        if case.body is None or case.meta is None:
            continue
        body_info = case.meta.components.get(ParameterLocation.BODY)
        if body_info is None or body_info.mode != GenerationMode.NEGATIVE:
            continue
        assert not validator.is_valid(case.body), f"NEGATIVE body is schema-valid: {case.body!r}"


def test_coverage_form_urlencoded_primitive_body_negative_no_crash(ctx):
    schema = ctx.openapi.load_schema(
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
    operation = schema["/convert"]["POST"]

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        case.as_curl_command()


def test_coverage_negative_string_above_max_length_invalid_when_pattern_quantifier_merged(ctx):
    # An unanchored quantifier like `{1,50}` doesn't prevent a 51-char string from passing
    # JSON Schema validation (partial match). The optimizer must anchor the pattern.
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
    operation = schema["/items"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
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
    operation = schema["/items"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), f"NEGATIVE body must be schema-invalid: {case.body!r}"


def test_coverage_negative_max_length_preserved_when_outer_optional_group_has_variable_inner(ctx):
    # Optional group with variable inner: minLength absorbed (? to {1}) but maxLength unrepresentable.
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
    operation = schema["/items"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
    assert len(cases) > 0
    for case in cases:
        assert not validator.is_valid(case.body), f"NEGATIVE body must be schema-invalid: {case.body!r}"


def test_coverage_negative_missing_required_with_additional_properties_schema(ctx):
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
    operation = schema["/items"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)
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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/items"]["GET"]
    query_param = next(p for p in operation.query if p.name == "name")
    optimized = query_param.optimized_schema
    validator = jsonschema_rs.validator_for(optimized)

    cases = _generate_cases(operation, GenerationMode.POSITIVE)
    positive_cases = [c for c in cases if c.query and "name" in c.query]
    assert len(positive_cases) > 0
    for case in positive_cases:
        assert validator.is_valid(case.query["name"]), (
            f"POSITIVE value {case.query['name']!r} failed optimized_schema validation — "
            f"pattern was likely corrupted by update_quantifier"
        )


def test_coverage_positive_pattern_with_variable_suffix_not_overconstrained(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/owners": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
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
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/owners"]["POST"]
    validator = _body_validator(operation, validate_formats=False)

    cases = _generate_cases(operation, GenerationMode.POSITIVE)
    assert len(cases) > 0
    for case in cases:
        if case.media_type == "application/json" and case.body is not None:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/configuration"]["GET"]

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)

    response = response_factory.requests(status_code=200)
    ctx_check = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/upload"]["POST"]

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)

    response = response_factory.requests(status_code=200)
    ctx_check = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

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
    schema = ctx.openapi.load_schema(
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
    operation = schema["/upload"]["POST"]

    cases = _generate_cases(operation, GenerationMode.NEGATIVE)

    response = response_factory.requests(status_code=200)
    ctx_check = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    for case in cases:
        if isinstance(case.body, dict):
            continue
        assert negative_data_rejection(ctx_check, response, case) is None, (
            f"False positive: body {case.body!r} ({type(case.body).__name__})"
        )


def test_coverage_positive_body_nested_allof_inner_required_preserved(ctx):
    # Required fields from the second inner $ref (e.g. 'direction') must appear in POSITIVE bodies
    # when a oneOf branch resolves to allOf[{$ref: base}, {$ref: extension}].
    schema = ctx.openapi.load_schema(
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
    operation = schema["/reports"]["POST"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.POSITIVE)
    for case in cases:
        if case.media_type != "application/json" or not case.meta:
            continue
        comp = case.meta.components.get(ParameterLocation.BODY)
        if comp and comp.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_string_type_with_empty_properties(ctx):
    # A property with type:string and properties:{} must generate a string value, not {}.
    # The properties keyword is irrelevant when type is not object.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items/{id}"]["put"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
    for case in cases:
        if case.body is None or not case.meta:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_required_unsatisfiable_array_enum(ctx):
    # POSITIVE bodies must satisfy `required` even when a property's schema is unsatisfiable.
    # The query parameter gives the coverage phase something else to negate.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/clients"]["post"]
    validator = _body_validator(operation)

    negative_cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]
    # Must complete without RecursionError
    _iter_cases(operation, GenerationMode.POSITIVE, generation_config=loaded.config.generation)


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
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.2",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "xml": {"name": "User"},
                                        "properties": {
                                            "a": {"type": "string"},
                                            "b": {"type": "string"},
                                            "c": {"type": "string"},
                                            "d": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = schema["/x"]["POST"]
    cases = _generate_cases(operation, GenerationMode.POSITIVE)
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.POSITIVE, generation_config=loaded.config.generation)
    for case in cases:
        if case.body is None or case.meta is None:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_coverage_positive_body_nested_required_unsatisfiable_field(ctx):
    # When a nested required field has an unsatisfiable schema (e.g. pattern+format contradiction),
    # the parent template must not include the incomplete sub-object as a POSITIVE value.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]
    validator = _body_validator(operation)

    cases = _iter_cases(operation, GenerationMode.POSITIVE, generation_config=loaded.config.generation)
    for case in cases:
        if case.body is None or case.meta is None:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.POSITIVE:
            assert validator.is_valid(case.body), f"POSITIVE body is schema-invalid: {case.body!r}"


def test_revalidation_preserves_negative_mode_for_format_violating_body(ctx):
    # A NEGATIVE body with a format-violating value ('' for a uuid field) must stay
    # NEGATIVE after body reassignment triggers _revalidate_metadata.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]

    cases = _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation)

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


def test_negative_coverage_emits_invalid_format_for_uuid_body_property(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["orderId"],
                                    "properties": {"orderId": {"type": "string", "format": "uuid"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = loaded["/items"]["post"]
    cases = _iter_cases(operation, GenerationMode.NEGATIVE)
    format_violators = [
        case
        for case in cases
        if case.meta is not None
        and case.meta.phase.data.scenario == CoverageScenario.INVALID_FORMAT
        and isinstance(case.body, dict)
        and "orderId" in case.body
    ]
    assert format_violators, "no INVALID_FORMAT case emitted for body property with format: uuid"
    value = format_violators[0].body["orderId"]
    with pytest.raises(ValueError):
        uuid.UUID(value)


def test_coverage_form_urlencoded_filters_primitives_with_bundled_ref(ctx):
    # Every NEGATIVE form-urlencoded body must remain schema-invalid after string coercion.
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/t"]["post"]
    validator = _body_validator(operation, "application/x-www-form-urlencoded")

    for case in _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation):
        if case.media_type != "application/x-www-form-urlencoded" or not isinstance(case.body, dict):
            continue
        bi = case.meta.components.get(ParameterLocation.BODY) if case.meta else None
        if not bi or bi.mode != GenerationMode.NEGATIVE:
            continue
        wire = {k: str(v) for k, v in case.body.items()}
        assert not validator.is_valid(wire), (
            f"NEGATIVE form-urlencoded body becomes schema-valid after string coercion: {case.body!r} -> {wire!r}"
        )


def test_coverage_form_urlencoded_filters_nested_wire_identical_mutations(ctx):
    # Every NEGATIVE form-urlencoded body must remain schema-invalid after string coercion.
    loaded = ctx.openapi.load_schema(
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
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = loaded["/t"]["post"]
    validator = _body_validator(operation, "application/x-www-form-urlencoded")

    for case in _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation):
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
    loaded = ctx.openapi.load_schema(
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
    operation = loaded["/items"]["post"]
    validator = _body_validator(operation)

    for case in _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation):
        if case.body is None or case.meta is None:
            continue
        bi = case.meta.components.get(ParameterLocation.BODY)
        if bi and bi.mode == GenerationMode.NEGATIVE:
            assert not validator.is_valid(case.body), (
                f"NEGATIVE body is schema-valid (mutation had no effect): {case.body!r}"
            )


def test_coverage_array_above_max_items_with_draft_mismatch_sibling(ctx):
    # When a sibling keyword breaks the auto-detected validator (e.g. `exclusiveMinimum: true`),
    # the `ARRAY_ABOVE_MAX_ITEMS` mutation must still produce a body whose target array exceeds
    # maxItems — spec-supplied examples whose arrays fit within bounds must not slip through.
    loaded = ctx.openapi.load_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
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
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = loaded["/r"]["post"]
    for case in _iter_cases(operation, GenerationMode.NEGATIVE, generation_config=loaded.config.generation):
        if case.meta is None:
            continue
        if str(getattr(case.meta.phase.data, "scenario", "")).endswith("ARRAY_ABOVE_MAX_ITEMS"):
            body_t = case.body.get("t") if isinstance(case.body, dict) else None
            assert body_t is not None and len(body_t) > 3, (
                f"ARRAY_ABOVE_MAX_ITEMS mutation produced a body within bounds: {case.body!r}"
            )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_consumes_path_keyed_pool(cli, snapshot_cli, ctx):
    paths = {
        "/widgets/{widgetId}": {
            "post": {
                "operationId": "createWidget",
                "parameters": [
                    {
                        "name": "widgetId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "responses": {"201": {"description": "Created"}},
            },
            "get": {
                "operationId": "getWidget",
                "parameters": [
                    {
                        "name": "widgetId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "404": {"description": "Not found"},
                },
            },
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    widgets: set[str] = set()

    @app.route("/widgets/<widget_id>", methods=["POST"])
    def create_widget(widget_id):
        widgets.add(widget_id)
        return "", 201

    @app.route("/widgets/<widget_id>", methods=["GET"])
    def get_widget(widget_id):
        if widget_id not in widgets:
            return "", 404
        # Planted bug: required `name` is null for widgets that exist
        return jsonify({"name": None}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c response_schema_conformance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_param_mutation_preserves_nested_overlay_siblings(cli, ctx, snapshot_cli):
    # Nested overlay must keep generator-produced siblings (`note`) when the pool seeds a foreign-key leaf (`location_id`).
    paths = {
        "/locations": {
            "post": {
                "operationId": "createLocation",
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id"],
                                    "properties": {"id": {"type": "integer"}},
                                }
                            }
                        }
                    }
                },
            }
        },
        "/departments": {
            "post": {
                "operationId": "createDepartment",
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
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["shipping"],
                                "properties": {
                                    "shipping": {
                                        "type": "object",
                                        "required": ["note"],
                                        "properties": {
                                            "location_id": {"type": "integer"},
                                            "note": {"type": "string"},
                                        },
                                    }
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/locations", methods=["POST"])
    def locations():
        return jsonify({"id": 42}), 201

    @app.route("/departments", methods=["POST"])
    def departments():
        body = request.get_json(silent=True)
        shipping = body.get("shipping") if isinstance(body, dict) else None
        if not isinstance(shipping, dict) or not isinstance(shipping.get("note"), str):
            return ("", 422)
        return ("", 201)

    assert cli.run_openapi_app(app, "--phases=coverage", "--continue-on-failure") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_consumes_body_field_keyed_pool(cli, snapshot_cli, ctx):
    paths = {
        "/sessions": {
            "post": {
                "operationId": "createSession",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["sessionId"],
                                "properties": {"sessionId": {"type": "string", "format": "uuid"}},
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/sessions/{sessionId}/events": {
            "post": {
                "operationId": "createEvent",
                "parameters": [
                    {
                        "name": "sessionId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["sessionId", "kind"],
                                "properties": {
                                    "sessionId": {"type": "string", "format": "uuid"},
                                    "kind": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "404": {"description": "Not found"},
                },
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    sessions: set[str] = set()

    @app.route("/sessions", methods=["POST"])
    def create_session():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return "", 400
        session_id = data.get("sessionId")
        if not isinstance(session_id, str):
            return "", 400
        sessions.add(session_id)
        return "", 201

    @app.route("/sessions/<session_id>/events", methods=["POST"])
    def create_event(session_id):
        if session_id not in sessions:
            return "", 404
        # Planted bug: required `name` is null when sessionId exists
        return jsonify({"name": None}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c response_schema_conformance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_correlates_nested_resource_pool_picks(cli, snapshot_cli, ctx):
    # Independent picks return (U2, R1) but R1's parent is U1; only correlation matches the planted pair.
    paths = {
        "/products": {
            "post": {
                "operationId": "createProduct",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["productId"],
                                "properties": {
                                    "productId": {
                                        "type": "string",
                                        "examples": [
                                            "alpha-product-7af3",
                                            "bravo-product-9c11",
                                        ],
                                    }
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/products/{productId}/reviews": {
            "post": {
                "operationId": "createReview",
                "parameters": [
                    {
                        "name": "productId",
                        "in": "path",
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
                                "required": ["reviewId"],
                                "properties": {
                                    "reviewId": {
                                        "type": "string",
                                        "examples": ["alpha-review-1234"],
                                    }
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/products/{productId}/reviews/{reviewId}": {
            "get": {
                "operationId": "getReview",
                "parameters": [
                    {
                        "name": "productId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "reviewId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "404": {"description": "Not found"},
                },
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    products: set[str] = set()
    reviews: set[tuple[str, str]] = set()

    @app.route("/products", methods=["POST"])
    def create_product():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return "", 400
        product_id = data.get("productId")
        if not isinstance(product_id, str):
            return "", 400
        products.add(product_id)
        return "", 201

    @app.route("/products/<product_id>/reviews", methods=["POST"])
    def create_review(product_id):
        if product_id not in products:
            return "", 404
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return "", 400
        review_id = data.get("reviewId")
        if not isinstance(review_id, str):
            return "", 400
        reviews.add((product_id, review_id))
        return "", 201

    @app.route("/products/<product_id>/reviews/<review_id>", methods=["GET"])
    def get_review(product_id, review_id):
        if (product_id, review_id) not in reviews:
            return "", 404
        # Planted bug: required `name` is null for matched parent-child pairs.
        return jsonify({"name": None}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c response_schema_conformance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_negative_does_not_pollute_pool_with_invalid_values(cli, snapshot_cli, ctx):
    # A permissive endpoint's negative mutations must not seed the pool with values a strict endpoint would later reject.
    paths = {
        "/payments": {
            "post": {
                "operationId": "createPayment",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["customerId"],
                                "properties": {"customerId": {"type": "string"}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/audit": {
            "post": {
                "operationId": "createAuditEntry",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["customerId"],
                                "properties": {"customerId": {"type": "string"}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/customers/{customerId}": {
            "get": {
                "operationId": "getCustomer",
                "parameters": [{"name": "customerId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/payments", methods=["POST"])
    def payments():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("customerId"), str):
            return "", 400
        return "", 200

    @app.route("/audit", methods=["POST"])
    def audit():
        return "", 200

    @app.route("/customers/<customer_id>", methods=["GET"])
    def get_customer(customer_id):
        return "", 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c positive_data_acceptance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_pool_overlay_respects_stricter_destination_constraints(cli, snapshot_cli, ctx):
    # A loose endpoint contributes a value valid only for itself; a stricter consumer must not adopt it.
    paths = {
        "/clients": {
            "post": {
                "operationId": "createClient",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["clientId"],
                                "properties": {"clientId": {"type": "string"}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/identity-providers": {
            "put": {
                "operationId": "putIdentityProvider",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["clientId"],
                                "properties": {"clientId": {"type": "string", "minLength": 1}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/clients/{clientId}": {
            "get": {
                "operationId": "getClient",
                "parameters": [{"name": "clientId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/clients", methods=["POST"])
    def create_client():
        return "", 200

    @app.route("/identity-providers", methods=["PUT"])
    def put_identity_provider():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("clientId"), str) or not data["clientId"]:
            return "", 400
        return "", 200

    @app.route("/clients/<client_id>", methods=["GET"])
    def get_client(client_id):
        return "", 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c positive_data_acceptance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_pool_overlay_respects_destination_format(cli, snapshot_cli, ctx):
    # A producer with no `format` constraint must not contribute values that violate a consumer's `format: uuid`.
    # The producer caps `txnId` length at 5 — no value can satisfy uuid (36 chars), so any pool injection fails.
    paths = {
        "/a-create": {
            "post": {
                "operationId": "createSession",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["txnId"],
                                "properties": {"txnId": {"type": "string", "maxLength": 5}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/z-confirm": {
            "post": {
                "operationId": "confirmAuth",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["txnId"],
                                "properties": {"txnId": {"type": "string", "format": "uuid"}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/sessions/{txnId}": {
            "get": {
                "operationId": "getSession",
                "parameters": [{"name": "txnId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/a-create", methods=["POST"])
    def create_session():
        return "", 200

    @app.route("/z-confirm", methods=["POST"])
    def confirm_auth():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return "", 400
        txn_id = data.get("txnId")
        if not isinstance(txn_id, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", txn_id
        ):
            return "", 400
        return "", 200

    @app.route("/sessions/<txn_id>", methods=["GET"])
    def get_session(txn_id):
        return "", 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            "-c positive_data_acceptance",
        )
        == snapshot_cli
    )


def test_coverage_pool_overlay_dict_value_with_undeclared_keys(ctx):
    # Pool object value for "address" contains "country", absent from the property schema.
    loaded = load_schema(
        ctx,
        request_body={
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "address": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            }
                        },
                    }
                }
            },
        },
    )
    operation = loaded["/foo"]["post"]

    class _FakeDataSource:
        def pick_correlated_values(self, *, operation):
            return PoolPick(values={(ParameterLocation.BODY, "address"): {"city": "London", "country": "UK"}})

        def pick_captured_value(self, *, operation, location, name, context_constraints):
            return None

    list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
            extra_data_source=_FakeDataSource(),
        )
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


def test_coverage_pool_draws_multi_slot_correlated(ctx):
    # Two resource-bound path params on one operation produce two PoolDraws on each yielded case.
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    post_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "userId": {"type": "string"}},
        "required": ["id", "userId"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {"201": {"content": {"application/json": {"schema": user_schema}}}},
                }
            },
            "/users/{userId}/posts": {
                "post": {
                    "operationId": "createPost",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"201": {"content": {"application/json": {"schema": post_schema}}}},
                }
            },
            "/users/{userId}/posts/{postId}": {
                "get": {
                    "operationId": "getPost",
                    "parameters": [
                        {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "postId", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    # The post-creation request used `userId=user-1` (path); store that on context so the
    # consumer's (userId, postId) pair stays correlated.
    data_source.repository.record_response(
        operation="POST /users/{userId}/posts",
        status_code=201,
        payload={"id": "post-7", "userId": "user-1"},
        context={"userId": "user-1"},
    )

    consumer = schema["/users/{userId}/posts/{postId}"]["GET"]
    cases = list(
        iter_coverage_cases(
            operation=consumer,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
            extra_data_source=data_source,
        )
    )
    assert cases
    # Both slots attribute back to the post-creating operation — its `id` for `postId` and
    # its captured `userId` context for the parent. Order across draws is incidental, so
    # compare a name-keyed view.
    expected_source = "POST /users/{userId}/posts"
    assert {d.parameter_name: d for d in cases[0].meta.pool_draws} == {
        "userId": PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="userId",
            resource_name="User",
            resource_field="id",
            source_operation=expected_source,
            source_status=201,
        ),
        "postId": PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="postId",
            resource_name="Post",
            resource_field="id",
            source_operation=expected_source,
            source_status=201,
        ),
    }


def test_coverage_attaches_pool_draws_to_consumer_cases(ctx):
    # Real ResourceRepository capture: POST captures `id`; coverage cases for GET /albums/{id}
    # carry pool-draw provenance pointing back to POST.
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/albums": {
                "post": {
                    "operationId": "createAlbum",
                    "responses": {
                        "201": {"description": "Created", "content": {"application/json": {"schema": user_schema}}}
                    },
                }
            },
            "/albums/{albumId}": {
                "get": {
                    "operationId": "getAlbum",
                    "parameters": [{"name": "albumId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation="POST /albums", status_code=201, payload={"id": "alb-42", "name": "First"}
    )

    consumer = schema["/albums/{albumId}"]["GET"]
    cases = list(
        iter_coverage_cases(
            operation=consumer,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
            extra_data_source=data_source,
        )
    )
    assert cases, "expected at least one coverage case for the consumer operation"
    # Every case from this generator carries the same pool-draw attribution.
    assert cases[0].meta.pool_draws == (
        PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="albumId",
            resource_name="Album",
            resource_field="id",
            source_operation="POST /albums",
            source_status=201,
        ),
    )


def test_pool_inventory_respects_operation_filters(ctx):
    # When the user filters operations, the inventory must intersect with the selected set —
    # otherwise the analyzer's coverage ratios treat intentionally excluded operations as
    # missing producers/consumers.
    item_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {"201": {"content": {"application/json": {"schema": item_schema}}}},
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/widgets/{widgetId}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [{"name": "widgetId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    full_inventory = schema._measure_statistic().resource_pool
    # Sanity: without filters the inventory holds both resource families.
    assert set(full_inventory.producer_labels) == {"POST /items", "POST /widgets"}
    assert set(full_inventory.consumer_labels) == {"GET /items/{itemId}", "GET /widgets/{widgetId}"}

    filtered = schema.include(path_regex="/items")._measure_statistic().resource_pool
    # The widget producers/consumers are excluded by the filter; coverage denominators
    # should follow the filter, not the full schema.
    assert filtered.producer_labels == ["POST /items"]
    assert filtered.consumer_labels == ["GET /items/{itemId}"]
    assert filtered.resources == 1


def test_coverage_pool_draws_survive_numeric_id_serialization(ctx):
    # Pooled numeric id arrives at the case as a stringified wire value; the analyzer must
    # see the draw attached anyway, otherwise chain-rate is under-reported for numeric APIs.
    item_schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {"201": {"content": {"application/json": {"schema": item_schema}}}},
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": 42})

    consumer = schema["/items/{itemId}"]["GET"]
    cases = list(
        iter_coverage_cases(
            operation=consumer,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
            extra_data_source=data_source,
        )
    )
    assert cases
    # Wire form is `"42"` (string) but the draw still attributes back to the integer producer.
    assert cases[0].meta.pool_draws == (
        PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="itemId",
            resource_name="Item",
            resource_field="id",
            source_operation="POST /items",
            source_status=201,
        ),
    )


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
        case.media_type for case in _iter_cases(operation, GenerationMode.POSITIVE) if case.body is not NOT_SET
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
    schema = ctx.openapi.from_full_schema(
        {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "components": {
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
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/Upload"}}},
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )
    operation = schema["/upload"]["POST"]
    config = SanitizationConfig(enabled=False)
    count = 0
    for case in iter_coverage_cases(
        operation=operation,
        generation_modes=list(GenerationMode),
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=operation.schema.config.generation,
    ):
        prepare_request(case, headers=None, config=config)
        count += 1
    assert count > 0


def test_explicit_content_type_header_does_not_collide_with_body_coverage(ctx):
    # When CT is declared as an explicit header parameter, body cases must keep CT pinned to the
    # body's media type, and CT-mutation cases must not also carry a body (the two sweeps are independent).
    schema = ctx.openapi.load_schema(
        {
            "/forgot": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "name": "Content-Type",
                            "in": "header",
                            "type": "string",
                            "enum": ["application/json", "application/xml"],
                            "default": "application/json",
                        },
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "schema": {"type": "object", "properties": {"email": {"type": "string"}}},
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    operation = schema["/forgot"]["POST"]
    body_cases_cts = set()
    ct_mutation_bodies = []
    for case in _iter_cases(operation, GenerationMode.POSITIVE) + _iter_cases(operation, GenerationMode.NEGATIVE):
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
    schema = ctx.openapi.load_schema(
        {
            "/filter": {
                "post": {
                    "parameters": [
                        {"name": "body", "in": "body", "required": True, "schema": {"$ref": "#/definitions/Filter"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
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
    operation = schema["/filter"]["POST"]
    validator = _body_validator(operation)

    negatives = [case for case in _iter_cases(operation, GenerationMode.NEGATIVE) if case.body is not NOT_SET]
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
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "array", "minItems": 2, "items": {"not": {}}}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["POST"]
    bodies = [case.body for case in _iter_cases(operation, GenerationMode.NEGATIVE) if case.body is not NOT_SET]
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
    return [
        case.body
        for case in iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
        )
        if isinstance(case.body, dict)
    ]


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
    raw = ctx.openapi.build_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["tools"],
                                    "properties": {"tools": {"$ref": "#/components/schemas/Tool"}},
                                }
                            }
                        },
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
        components=_tool_components(tag_keyword),
    )
    bodies = _discriminator_positive_bodies(ctx.openapi.from_full_schema(raw)["/r"]["POST"])
    tags = {body["tools"]["type"] for body in bodies if isinstance(body.get("tools"), dict) and "type" in body["tools"]}
    assert tags == expected_tags, f"expected {expected_tags}; got tags={tags}, bodies={bodies}"


def test_discriminator_polymorphic_items_array_covers_each_branch(ctx):
    raw = ctx.openapi.build_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["tools"],
                                    "properties": {
                                        "tools": {"type": "array", "items": {"$ref": "#/components/schemas/Tool"}},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
        components=_tool_components("enum"),
    )
    bodies = _discriminator_positive_bodies(ctx.openapi.from_full_schema(raw)["/r"]["POST"])
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
    raw = ctx.openapi.build_schema(
        {
            "/r": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["tools"],
                                    "properties": {"tools": {"$ref": "#/components/schemas/Tool"}},
                                }
                            }
                        },
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
        components=components,
    )
    bodies = _discriminator_positive_bodies(ctx.openapi.from_full_schema(raw)["/r"]["POST"])
    tags = {body["tools"]["type"] for body in bodies if isinstance(body.get("tools"), dict) and "type" in body["tools"]}
    assert tags == {"web_search"}, f"mapping must override branch const; got tags={tags}, bodies={bodies}"


def test_negative_coverage_violates_int64_format_bounds(ctx):
    # The range implied by `format: int64` must reach negative generation as real bounds,
    # so out-of-range integers stay covered as boundary violations instead of positive data.
    schema = ctx.openapi.load_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"value": {"type": "integer", "format": "int64"}},
                                    "required": ["value"],
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
    cases = _iter_cases(schema["/x"]["POST"], GenerationMode.NEGATIVE)

    violations = {
        case.meta.phase.data.scenario: case.body["value"]
        for case in cases
        if isinstance(case.body, dict) and isinstance(case.body.get("value"), int)
    }
    assert violations[CoverageScenario.VALUE_ABOVE_MAXIMUM] == 2**63
    assert violations[CoverageScenario.VALUE_BELOW_MINIMUM] == -(2**63) - 1
    assert all(case.meta.generation.mode == GenerationMode.NEGATIVE for case in cases)
