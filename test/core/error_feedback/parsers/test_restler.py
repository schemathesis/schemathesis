from __future__ import annotations

from typing import Any

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.confluent import ConfluentParser
from schemathesis.core.error_feedback.parsers.flask_rest import FlaskRestParser
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.litestar import LitestarParser
from schemathesis.core.error_feedback.parsers.marshmallow import MarshmallowParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
)
from schemathesis.core.error_feedback.parsers.restler import RestlerParser
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


# Wire envelopes captured from running Luracast Restler apps. v3/v5 prefix the reason phrase, v6 does not;
# `debug` is only present outside production mode.
def _restler(message: str, *, version: str = "v3") -> dict:
    if version == "v6":
        return {
            "error": {"code": 400, "message": message},
            "debug": {
                "source": "Validator.php:361",
                "trace": [{"file": "Route.php:476", "function": "Validator::validate", "args": [0]}],
            },
        }
    body: dict[str, Any] = {"error": {"code": 400, "message": f"Bad Request: {message}"}}
    if version == "v3":
        body["debug"] = {
            "source": "Validator.php:718 at validate stage",
            "stages": {"success": ["get", "route", "negotiate"], "failure": ["validate", "message"]},
        }
    return body


_RESTLER_VERSIONS = pytest.mark.parametrize("version", ["v3", "v3-production", "v6"])


def _invalid(name: str, detail: str) -> str:
    return f"Invalid value specified for `{name}`. {detail}"


_RESTLER_MESSAGES = [
    pytest.param("`subject` is required.", (("subject",), ObservationKind.MUST_NOT_BE_BLANK, None), id="required"),
    pytest.param(
        _invalid("priority", "Expecting integer value"),
        (("priority",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
        id="integer-type",
    ),
    pytest.param(
        _invalid("ratio", "Expecting numeric value"),
        (("ratio",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),
        id="numeric-type",
    ),
    pytest.param(
        _invalid("active", "Expecting boolean value"),
        (("active",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="boolean")),
        id="boolean-type",
    ),
    pytest.param(
        _invalid("name", "Expecting alpha numeric value"),
        (("name",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),
        id="string-type",
    ),
    pytest.param(
        "Invalid value specified for tags. Expecting items of type `string`",
        (("tags",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="array")),
        id="array-type",
    ),
    pytest.param(
        _invalid("priority", "Minimum required value is 1."),
        (
            ("priority",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
        ),
        id="minimum",
    ),
    pytest.param(
        _invalid("ratio", "Maximum allowed value is 0.5."),
        (
            ("ratio",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=0.5, direction=BoundDirection.MAX, exclusive=False),
        ),
        id="maximum",
    ),
    pytest.param(
        _invalid("name", "Minimum 3 characters required."),
        (("name",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
        id="min-length",
    ),
    pytest.param(
        _invalid("code", "Maximum 1 character allowed."),
        (("code",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=1)),
        id="max-length-singular",
    ),
    pytest.param(
        _invalid("tags", "Minimum 2 items required."),
        (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=2, max=None)),
        id="min-items",
    ),
    pytest.param(
        _invalid("tags", "Maximum 5 items allowed."),
        (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),
        id="max-items",
    ),
    pytest.param(
        "Invalid value specified for tags. Minimum 2 items required.",
        (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=2, max=None)),
        id="min-count-unquoted-name",
    ),
    pytest.param(
        _invalid("level", "Expected one of (low,high)."),
        (("level",), ObservationKind.ENUM, EnumPayload(values=("low", "high"))),
        id="choice",
    ),
    pytest.param(
        _invalid("email", "Expecting email in `name@example.com` format"),
        (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
        id="email-format",
    ),
    pytest.param(
        _invalid("token", "Expecting a Universally Unique IDentifier (UUID) string."),
        (("token",), ObservationKind.FORMAT, FormatPayload(name="uuid")),
        id="uuid-format",
    ),
    pytest.param(
        _invalid("site", "Expecting url in `http://example.com` format"),
        (("site",), ObservationKind.FORMAT, FormatPayload(name="uri")),
        id="url-format",
    ),
    pytest.param(
        _invalid("due", "Expecting date in `YYYY-MM-DD` format, such as `2026-09-16`"),
        (("due",), ObservationKind.FORMAT, FormatPayload(name="date")),
        id="date-format",
    ),
    pytest.param(
        _invalid("alpha", "Expecting only alphabetic characters."),
        (("alpha",), ObservationKind.PATTERN, PatternPayload(regex="^[a-zA-Z]+$")),
        id="alpha",
    ),
    pytest.param(
        _invalid("alnum", "Expecting only alpha numeric characters."),
        (("alnum",), ObservationKind.PATTERN, PatternPayload(regex="^[a-zA-Z0-9]+$")),
        id="alphanumeric",
    ),
    pytest.param(
        _invalid("digits", "Expecting only numeric characters."),
        (("digits",), ObservationKind.PATTERN, PatternPayload(regex="^[0-9]+$")),
        id="numeric-characters",
    ),
    pytest.param(
        _invalid("printable", "Expecting only printable characters."),
        (("printable",), ObservationKind.PATTERN, PatternPayload(regex="^[ -~]+$")),
        id="printable",
    ),
    pytest.param(
        _invalid("hex", "Expecting only hexadecimal digits."),
        (("hex",), ObservationKind.PATTERN, PatternPayload(regex="^[0-9a-fA-F]+$")),
        id="hex",
    ),
    pytest.param(
        _invalid("color", "Expecting color as hexadecimal digits."),
        (("color",), ObservationKind.PATTERN, PatternPayload(regex="^#[0-9a-fA-F]{6}$")),
        id="color",
    ),
    pytest.param(
        _invalid("tel", "Expecting phone number, a numeric value with optional `+` prefix"),
        (("tel",), ObservationKind.PATTERN, PatternPayload(regex=r"^\+?[0-9]+$")),
        id="tel",
    ),
    pytest.param(
        _invalid("time", "Expecting time in `HH:MM:SS` format, such as `13:17:07`"),
        (("time",), ObservationKind.PATTERN, PatternPayload(regex="^([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")),
        id="time",
    ),
    pytest.param(
        _invalid("time12", "Expecting time in 12 hour format, such as `08:00AM` and `10:05:11`"),
        (
            ("time12",),
            ObservationKind.PATTERN,
            PatternPayload(regex="^([1-9]|1[0-2]|0[1-9])(:[0-5][0-9])? ?([aApP][mM])?$"),
        ),
        id="time12",
    ),
    pytest.param(
        _invalid("datetime", "Expecting date and time in `YYYY-MM-DD HH:MM:SS` format, such as `2026-09-16 13:17:07`"),
        (
            ("datetime",),
            ObservationKind.PATTERN,
            PatternPayload(
                regex="^(19[0-9]{2}|20[0-9]{2})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]) ([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$"
            ),
        ),
        id="datetime",
    ),
    pytest.param(
        _invalid("ip", "Expecting IP address in IPV6 or IPV4 format"),
        (("ip",), ObservationKind.FORMAT, FormatPayload(name="ipv4")),
        id="ip",
    ),
    pytest.param(
        _invalid("timestamp", "Expecting unix timestamp, such as 1789564627"),
        (("timestamp",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
        id="timestamp",
    ),
    pytest.param(
        _invalid("address[email]", "Expecting email in `name@example.com` format"),
        (("address", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),
        id="nested-object-field",
    ),
    pytest.param(
        _invalid("tags[1]", "Expecting alpha numeric value"),
        (("tags", 1), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),
        id="array-element",
    ),
    # Hand-written body checks common in Restler apps.
    pytest.param(
        "lastname field missing", (("lastname",), ObservationKind.MUST_NOT_BE_BLANK, None), id="field-missing"
    ),
    pytest.param(
        "label field absent in json at root level",
        (("label",), ObservationKind.MUST_NOT_BE_BLANK, None),
        id="field-absent-at-root",
    ),
]


@_RESTLER_VERSIONS
@pytest.mark.parametrize(("message", "expected"), _RESTLER_MESSAGES)
def test_restler_parser_can_parse_recognises_envelope(message, expected, version):
    assert RestlerParser().can_parse(body=_restler(message, version=version)) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        _restler("Thirdparty ID is mandatory"),
        # A path parameter of the wrong type names no expected type.
        _restler("invalid value specified for `id`"),
        _restler("Invalid value specified for `address`"),
        # Sent for both an empty object and a list.
        _restler("Invalid value specified for `address`. Expecting an item of type `Address`"),
        _restler("JSON Parser: Syntax error", version="v6"),
        {"error": {"code": 405, "message": "Method Not Allowed"}},
        {"error": {"code": "400", "message": "Bad Request: `subject` is required."}},
        {"error": "Bad Request: `subject` is required."},
        {"error": {"code": 400, "message": "Invalid value at 'name'", "status": "INVALID_ARGUMENT"}},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "unrecognised-message",
        "path-parameter-type",
        "no-detail",
        "object-expected",
        "malformed-json",
        "method-not-allowed",
        "stringy-code",
        "error-not-object",
        "google-api-error",
    ],
)
def test_restler_parser_can_parse_rejects_non_restler_bodies(body):
    assert RestlerParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 400, "message": 5}},
        _restler("Thirdparty ID is mandatory"),
    ],
    ids=["non-string-message", "unrecognised-message"],
)
def test_restler_parser_parse_ignores_unclassifiable_bodies(make_operation, case_factory, body):
    assert parse_observations(RestlerParser(), body, make_operation, case_factory) == ()


@_RESTLER_VERSIONS
@pytest.mark.parametrize(("message", "expected"), _RESTLER_MESSAGES)
def test_restler_parser_parse(make_operation, case_factory, message, expected, version):
    assert tuple(
        (o.location, o.parameter_path, o.kind, o.payload)
        for o in parse_observations(RestlerParser(), _restler(message, version=version), make_operation, case_factory)
    ) == ((ParameterLocation.BODY, *expected),)


@pytest.mark.parametrize(
    "parser",
    [
        AjvParser(),
        AspNetParser(),
        ConfluentParser(),
        FlaskRestParser(),
        GoValidatorParser(),
        JacksonParser(),
        LaravelParser(),
        LitestarParser(),
        MarshmallowParser(),
        PydanticParser(),
        RailsParser(),
        SpringParser(),
        SymfonyParser(),
        ZodParser(),
    ],
    ids=lambda p: type(p).__name__,
)
@_RESTLER_VERSIONS
@pytest.mark.parametrize(("message", "expected"), _RESTLER_MESSAGES)
def test_other_parsers_reject_restler_bodies(parser, message, expected, version):
    assert parser.can_parse(body=_restler(message, version=version)) is False
