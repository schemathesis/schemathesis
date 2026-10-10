from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
)
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.confluent import ConfluentParser
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
)
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations

# Wire-form envelopes captured against a live cp-kafka-rest; older-release wordings
# carried in alongside the current ones so both arms of each regex stay covered.
_CONFLUENT_P4_ALTER_OPERATION = {
    "error_code": 400,
    "message": (
        'Cannot coerce empty String ("") to `AlterOperation` value '
        "(but could if coercion was enabled using `CoercionConfig`)"
    ),
}


_CONFLUENT_P4_PERMISSION = {
    "error_code": 400,
    "message": (
        'Cannot coerce empty String ("") to `Permission` value '
        "(but could if coercion was enabled using `CoercionConfig`)"
    ),
}


_CONFLUENT_P4_PATTERN_TYPE = {
    "error_code": 400,
    "message": (
        'Cannot coerce empty String ("") to `PatternType` value '
        "(but could if coercion was enabled using `CoercionConfig`)"
    ),
}


_CONFLUENT_P5_NULL_INPUT = {
    "error_code": 42206,
    "message": "Payload error. Null input provided. Data is required.",
}


_CONFLUENT_P5_EMPTY_INPUT = {
    "error_code": 42206,
    "message": "Payload error. Empty input provided. Data is required.",
}


_CONFLUENT_P5_EMPTY_BATCH = {
    "error_code": 42208,
    "message": "Empty batch.",
}


_CONFLUENT_P5_BATCH_ENTRY = {
    "error_code": 42208,
    "message": "Batch entry identifier is not a valid string.",
}


_CONFLUENT_P6_RESOURCE_TYPE_ANY = {
    "error_code": 400,
    "message": "resource_type cannot be ANY",
}


_CONFLUENT_P6_PATTERN_TYPE_ANY = {
    "error_code": 400,
    "message": "pattern_type cannot be ANY",
}


_CONFLUENT_P7_TOPIC_NAME_OLD = {
    "error_code": 40002,
    "message": (
        "Topic name is invalid: '[Å󠱑Q' contains one or more characters other than ASCII alphanumerics, '.', '_' and '-'"
    ),
}


_CONFLUENT_P7_TOPIC_NAME_NEW = {
    "error_code": 42206,
    "message": "Payload error. Invalid topic name.",
}


_CONFLUENT_ACCEPTED_BODIES = [
    pytest.param(_CONFLUENT_P4_ALTER_OPERATION, id="p4-alter-operation"),
    pytest.param(_CONFLUENT_P4_PERMISSION, id="p4-permission"),
    pytest.param(_CONFLUENT_P4_PATTERN_TYPE, id="p4-pattern-type"),
    pytest.param(_CONFLUENT_P5_NULL_INPUT, id="p5-null-input"),
    pytest.param(_CONFLUENT_P5_EMPTY_INPUT, id="p5-empty-input"),
    pytest.param(_CONFLUENT_P5_EMPTY_BATCH, id="p5-empty-batch"),
    pytest.param(_CONFLUENT_P5_BATCH_ENTRY, id="p5-batch-entry"),
    pytest.param(_CONFLUENT_P6_RESOURCE_TYPE_ANY, id="p6-resource-type-any"),
    pytest.param(_CONFLUENT_P6_PATTERN_TYPE_ANY, id="p6-pattern-type-any"),
    pytest.param(_CONFLUENT_P7_TOPIC_NAME_OLD, id="p7-topic-name-old"),
    pytest.param(_CONFLUENT_P7_TOPIC_NAME_NEW, id="p7-topic-name-new"),
]


@pytest.mark.parametrize("body", _CONFLUENT_ACCEPTED_BODIES)
def test_confluent_parser_can_parse_recognises_envelope(body):
    assert ConfluentParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"error_code": 200, "message": "ok"},
        {"error_code": 500, "message": "Internal Server Error"},
        {"error_code": 50000, "message": "Internal Server Error"},
        {"error_code": "400", "message": "Cannot coerce empty String"},
        {"error_code": 400, "message": None},
        {"error_code": 400, "message": "the rest proxy is on fire"},
        {"error_code": True, "message": "bool error_code"},
        {"message": "no error_code"},
        {"error_code": 400},
        {"detail": "Spring style"},
        [{"propertyPath": "email", "code": "abc"}],
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "code-200",
        "code-500",
        "code-50000",
        "stringy-code",
        "non-string-message",
        "unrecognised-message",
        "boolean-error-code",
        "missing-error-code",
        "missing-message",
        "spring-shape",
        "symfony-shape",
    ],
)
def test_confluent_parser_can_parse_rejects_non_confluent_bodies(body):
    assert ConfluentParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    ("body", "case_kwargs", "expected"),
    [
        pytest.param(
            _CONFLUENT_P4_ALTER_OPERATION,
            {"body": {"data": [{"name": "compression.type", "operation": "", "value": "gzip"}]}},
            ((("data", 0, "operation"), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="p4-alter-operation-attributes-empty-string-field",
        ),
        pytest.param(
            _CONFLUENT_P4_PERMISSION,
            {
                "body": {
                    "resource_type": "TOPIC",
                    "resource_name": "t",
                    "pattern_type": "LITERAL",
                    "principal": "User:bob",
                    "host": "*",
                    "operation": "READ",
                    "permission": "",
                }
            },
            ((("permission",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="p4-permission-attributes-empty-string-field",
        ),
        pytest.param(
            _CONFLUENT_P5_EMPTY_BATCH,
            {"body": {"data": []}},
            ((("data",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="p5-empty-batch-narrows-data-array",
        ),
        pytest.param(
            _CONFLUENT_P7_TOPIC_NAME_OLD,
            {"body": {"topic_name": "[Å󠱑Q", "partitions_count": 1, "replication_factor": 1}},
            ((("topic_name",), ObservationKind.PATTERN, PatternPayload(regex="^[a-zA-Z0-9._-]+$")),),
            id="p7-topic-name-old-pins-pattern",
        ),
        pytest.param(
            _CONFLUENT_P7_TOPIC_NAME_NEW,
            {"body": {"topic_name": "foo!bar", "partitions_count": 1, "replication_factor": 1}},
            ((("topic_name",), ObservationKind.PATTERN, PatternPayload(regex="^[a-zA-Z0-9._-]+$")),),
            id="p7-topic-name-new-pins-pattern",
        ),
    ],
)
def test_confluent_parser_parse(make_operation, case_factory, body, case_kwargs, expected):
    operation = make_operation(method="post", path="/api/users")
    case = case_factory(**case_kwargs)
    actual = ConfluentParser().parse(operation=operation, body=body, case=case)
    actual_signatures = tuple((o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


@pytest.mark.parametrize(
    "body",
    [
        _CONFLUENT_P5_NULL_INPUT,
        _CONFLUENT_P5_EMPTY_INPUT,
        _CONFLUENT_P5_BATCH_ENTRY,
        _CONFLUENT_P6_RESOURCE_TYPE_ANY,
        _CONFLUENT_P6_PATTERN_TYPE_ANY,
    ],
    ids=[
        "p5-null-input",
        "p5-empty-input",
        "p5-batch-entry",
        "p6-resource-type",
        "p6-pattern-type",
    ],
)
def test_confluent_parser_recognises_unattributable_envelope_without_observations(make_operation, case_factory, body):
    actual = ConfluentParser().parse(operation=make_operation(), body=body, case=case_factory(body={}))
    assert actual == ()


def test_confluent_parser_p4_drops_when_multiple_empty_strings_match(make_operation, case_factory):
    body_with_two_empty_fields = {"data": [{"name": "compression.type", "operation": "", "value": ""}]}
    actual = ConfluentParser().parse(
        operation=make_operation(),
        body=_CONFLUENT_P4_ALTER_OPERATION,
        case=case_factory(body=body_with_two_empty_fields),
    )
    assert actual == ()


def test_confluent_parser_p4_drops_when_no_empty_string_in_body(make_operation, case_factory):
    actual = ConfluentParser().parse(
        operation=make_operation(),
        body=_CONFLUENT_P4_ALTER_OPERATION,
        case=case_factory(body={"data": [{"operation": "SET"}]}),
    )
    assert actual == ()


def test_confluent_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(ConfluentParser(), {"detail": "nope"}, make_operation, case_factory) == ()


def test_confluent_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = ConfluentParser().parse(
        operation=make_operation(method="get", path="/api/topics"),
        body=_CONFLUENT_P7_TOPIC_NAME_OLD,
        case=case_factory(query={"topic_name": "foo!bar"}),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


@pytest.mark.parametrize(
    "parser",
    [
        AjvParser(),
        AspNetParser(),
        GoValidatorParser(),
        LaravelParser(),
        RailsParser(),
        PydanticParser(),
        JacksonParser(),
        SymfonyParser(),
        ZodParser(),
    ],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _CONFLUENT_ACCEPTED_BODIES)
def test_other_parsers_reject_confluent_bodies(parser, body):
    assert parser.can_parse(body=body) is False
