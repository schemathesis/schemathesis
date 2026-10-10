from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
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

_SYMFONY_NOT_BLANK_CODE = "c1051bb4-d103-4f74-8988-acbcafc7fdc3"


_SYMFONY_EMAIL_CODE = "bd79c0ab-ddba-46cc-a703-a7a4b08de310"


_SYMFONY_URL_CODE = "57c2f299-1154-4870-89bb-ef3b1f5ad229"


_SYMFONY_UUID_CODE = "51120b12-a2bc-41bf-aa53-cd73daf330d0"


_SYMFONY_DATE_CODE = "69819696-02ac-4a99-9ff0-14e127c4d1bc"


_SYMFONY_DATETIME_CODE = "1a9da513-2640-4f84-9b6a-4d99dcddc628"


_SYMFONY_LENGTH_MIN_CODE = "9ff3fdc4-b214-49db-8718-39c315e33d45"


_SYMFONY_LENGTH_MAX_CODE = "d94b19cc-114f-4f44-9cc4-4138e80a87b9"


_SYMFONY_GTE_CODE = "ea4e51d1-3342-48bd-87f1-9e672cd90cad"


_SYMFONY_LTE_CODE = "30fbb013-d015-4232-8b3b-8f3be97a7e14"


_SYMFONY_GT_CODE = "778b7ae0-84d3-481a-9dec-35fdb64b1d78"


_SYMFONY_LT_CODE = "079d7420-2d13-460c-8756-de810eeb37d2"


_SYMFONY_RANGE_CODE = "04b91c99-a946-4221-afc5-e65ebac401eb"


_SYMFONY_CHOICE_CODE = "8e179f1b-97aa-4560-a02f-2a8b42e49df7"


_SYMFONY_REGEX_CODE = "de1e3db3-5ed4-4941-aae4-59f3667cc3a3"


_SYMFONY_TYPE_CODE = "ba785a8c-82cb-4283-967c-3cf342181b40"


_SYMFONY_COUNT_MIN_CODE = "bef8e338-6ae5-4caf-b8e2-50e7b0579e69"


_SYMFONY_COUNT_MAX_CODE = "756b1212-697c-468d-a9ad-50dd783bb169"


def _symfony_default(*violations: dict) -> list[dict]:
    return list(violations)


def _symfony_api_platform(*violations: dict) -> dict:
    return {
        "type": "https://symfony.com/errors/validation",
        "title": "Validation Failed",
        "detail": " | ".join(v.get("title", v.get("message", "")) for v in violations),
        "violations": list(violations),
    }


def _violation(property_path: str, code: str, message: str = "", **parameters: object) -> dict:
    return {
        "propertyPath": property_path,
        "message": message or "validation failed",
        "code": code,
        "parameters": {f"{{{{ {k} }}}}": v for k, v in parameters.items()},
    }


def _api_platform_violation(property_path: str, code: str, **parameters: object) -> dict:
    return {
        "propertyPath": property_path,
        "title": "validation failed",
        "template": "validation failed",
        "type": f"urn:uuid:{code}",
        "parameters": {f"{{{{ {k} }}}}": v for k, v in parameters.items()},
    }


_SYMFONY_DEFAULT_NOT_BLANK = _symfony_default(_violation("email", _SYMFONY_NOT_BLANK_CODE))


_SYMFONY_DEFAULT_EMAIL = _symfony_default(_violation("email", _SYMFONY_EMAIL_CODE))


_SYMFONY_DEFAULT_URL = _symfony_default(_violation("url", _SYMFONY_URL_CODE))


_SYMFONY_DEFAULT_UUID = _symfony_default(_violation("token", _SYMFONY_UUID_CODE))


_SYMFONY_DEFAULT_DATE = _symfony_default(_violation("when", _SYMFONY_DATE_CODE))


_SYMFONY_DEFAULT_DATETIME = _symfony_default(_violation("started", _SYMFONY_DATETIME_CODE, format='"Y-m-d H:i:s"'))


_SYMFONY_DEFAULT_LENGTH_MIN = _symfony_default(
    _violation("username", _SYMFONY_LENGTH_MIN_CODE, max="20", value='"ab"', limit="3", min="3", value_length="2")
)


_SYMFONY_DEFAULT_LENGTH_MAX = _symfony_default(
    _violation("username", _SYMFONY_LENGTH_MAX_CODE, min="3", limit="20", max="20", value_length="50")
)


_SYMFONY_DEFAULT_GTE = _symfony_default(_violation("age", _SYMFONY_GTE_CODE, value="-1", compared_value="0"))


_SYMFONY_DEFAULT_LTE = _symfony_default(_violation("age", _SYMFONY_LTE_CODE, value="200", compared_value="130"))


_SYMFONY_DEFAULT_GT = _symfony_default(_violation("score", _SYMFONY_GT_CODE, value="0", compared_value="0"))


_SYMFONY_DEFAULT_LT = _symfony_default(_violation("score", _SYMFONY_LT_CODE, value="100", compared_value="100"))


_SYMFONY_DEFAULT_RANGE = _symfony_default(_violation("quantity", _SYMFONY_RANGE_CODE, value="200", min="1", max="100"))


_SYMFONY_DEFAULT_CHOICE = _symfony_default(
    _violation("role", _SYMFONY_CHOICE_CODE, value='"superuser"', choices='"admin", "user", "guest"')
)


_SYMFONY_DEFAULT_REGEX = _symfony_default(
    _violation("code", _SYMFONY_REGEX_CODE, value='"lower"', pattern="/^[A-Z]{3}$/")
)


_SYMFONY_DEFAULT_TYPE = _symfony_default(_violation("count", _SYMFONY_TYPE_CODE, value='"twenty"', type="integer"))


_SYMFONY_DEFAULT_COUNT_MIN = _symfony_default(_violation("tags", _SYMFONY_COUNT_MIN_CODE, count="0", limit="1"))


_SYMFONY_DEFAULT_COUNT_MAX = _symfony_default(_violation("tags", _SYMFONY_COUNT_MAX_CODE, count="6", limit="5"))


_SYMFONY_DEFAULT_NESTED_PATH = _symfony_default(_violation("user.email", _SYMFONY_EMAIL_CODE))


_SYMFONY_DEFAULT_ARRAY_INDEX = _symfony_default(_violation("tags[0]", _SYMFONY_NOT_BLANK_CODE))


_SYMFONY_DEFAULT_MULTI_FIELD = _symfony_default(
    _violation("email", _SYMFONY_EMAIL_CODE),
    _violation("username", _SYMFONY_LENGTH_MIN_CODE, limit="3", min="3", max="20"),
    _violation("name", _SYMFONY_NOT_BLANK_CODE),
    _violation("age", _SYMFONY_GTE_CODE, compared_value="0"),
)


_SYMFONY_API_NOT_BLANK = _symfony_api_platform(_api_platform_violation("email", _SYMFONY_NOT_BLANK_CODE))


_SYMFONY_API_EMAIL = _symfony_api_platform(_api_platform_violation("email", _SYMFONY_EMAIL_CODE))


_SYMFONY_API_LENGTH_MIN = _symfony_api_platform(
    _api_platform_violation("username", _SYMFONY_LENGTH_MIN_CODE, limit="3", min="3", max="20")
)


_SYMFONY_API_GTE = _symfony_api_platform(_api_platform_violation("age", _SYMFONY_GTE_CODE, compared_value="0"))


_SYMFONY_API_RANGE = _symfony_api_platform(_api_platform_violation("quantity", _SYMFONY_RANGE_CODE, min="1", max="100"))


_SYMFONY_API_CHOICE = _symfony_api_platform(
    _api_platform_violation("role", _SYMFONY_CHOICE_CODE, choices='"admin", "user", "guest"')
)


_SYMFONY_API_REGEX = _symfony_api_platform(_api_platform_violation("code", _SYMFONY_REGEX_CODE, pattern="/^[A-Z]{3}$/"))


_SYMFONY_API_MULTI_FIELD = _symfony_api_platform(
    _api_platform_violation("email", _SYMFONY_EMAIL_CODE),
    _api_platform_violation("username", _SYMFONY_LENGTH_MIN_CODE, limit="3", min="3", max="20"),
    _api_platform_violation("name", _SYMFONY_NOT_BLANK_CODE),
    _api_platform_violation("tags", _SYMFONY_COUNT_MIN_CODE, count="0", limit="1"),
)


_SYMFONY_ACCEPTED_BODIES = [
    pytest.param(_SYMFONY_DEFAULT_NOT_BLANK, id="default-not-blank"),
    pytest.param(_SYMFONY_DEFAULT_EMAIL, id="default-email"),
    pytest.param(_SYMFONY_DEFAULT_URL, id="default-url"),
    pytest.param(_SYMFONY_DEFAULT_UUID, id="default-uuid"),
    pytest.param(_SYMFONY_DEFAULT_DATE, id="default-date"),
    pytest.param(_SYMFONY_DEFAULT_DATETIME, id="default-datetime"),
    pytest.param(_SYMFONY_DEFAULT_LENGTH_MIN, id="default-length-min"),
    pytest.param(_SYMFONY_DEFAULT_LENGTH_MAX, id="default-length-max"),
    pytest.param(_SYMFONY_DEFAULT_GTE, id="default-gte"),
    pytest.param(_SYMFONY_DEFAULT_LTE, id="default-lte"),
    pytest.param(_SYMFONY_DEFAULT_GT, id="default-gt"),
    pytest.param(_SYMFONY_DEFAULT_LT, id="default-lt"),
    pytest.param(_SYMFONY_DEFAULT_RANGE, id="default-range"),
    pytest.param(_SYMFONY_DEFAULT_CHOICE, id="default-choice"),
    pytest.param(_SYMFONY_DEFAULT_REGEX, id="default-regex"),
    pytest.param(_SYMFONY_DEFAULT_TYPE, id="default-type"),
    pytest.param(_SYMFONY_DEFAULT_COUNT_MIN, id="default-count-min"),
    pytest.param(_SYMFONY_DEFAULT_COUNT_MAX, id="default-count-max"),
    pytest.param(_SYMFONY_DEFAULT_NESTED_PATH, id="default-nested-path"),
    pytest.param(_SYMFONY_DEFAULT_ARRAY_INDEX, id="default-array-index"),
    pytest.param(_SYMFONY_DEFAULT_MULTI_FIELD, id="default-multi-field"),
    pytest.param(_SYMFONY_API_NOT_BLANK, id="api-not-blank"),
    pytest.param(_SYMFONY_API_EMAIL, id="api-email"),
    pytest.param(_SYMFONY_API_LENGTH_MIN, id="api-length-min"),
    pytest.param(_SYMFONY_API_GTE, id="api-gte"),
    pytest.param(_SYMFONY_API_RANGE, id="api-range"),
    pytest.param(_SYMFONY_API_CHOICE, id="api-choice"),
    pytest.param(_SYMFONY_API_REGEX, id="api-regex"),
    pytest.param(_SYMFONY_API_MULTI_FIELD, id="api-multi-field"),
]


@pytest.mark.parametrize("body", _SYMFONY_ACCEPTED_BODIES)
def test_symfony_parser_can_parse_recognises_envelope(body):
    assert SymfonyParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        [{"propertyPath": "email"}],
        [{"propertyPath": "email", "code": ""}],
        [{"message": "no propertyPath"}],
        {"violations": []},
        {"violations": [{"propertyPath": "email"}]},
        {"name": ["This field is required."]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
        {"errors": [{"keyword": "format", "instancePath": "/x", "params": {}}]},
        {"errors": [{"code": "invalid_string", "path": ["email"]}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "violation-without-code",
        "violation-with-empty-code",
        "violation-without-property-path",
        "violations-empty-list",
        "violations-no-code-or-type",
        "drf",
        "pydantic",
        "ajv-shape",
        "zod-shape",
    ],
)
def test_symfony_parser_can_parse_rejects_non_symfony_bodies(body):
    assert SymfonyParser().can_parse(body=body) is False


def _symfony_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _SYMFONY_DEFAULT_NOT_BLANK,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="default-not-blank",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="default-email",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_URL,
            ((("url",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="default-url",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_UUID,
            ((("token",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="default-uuid",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_DATE,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date")),),
            id="default-date",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_DATETIME,
            ((("started",), ObservationKind.FORMAT, FormatPayload(name="date-time")),),
            id="default-datetime",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LENGTH_MIN,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="default-length-min",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LENGTH_MAX,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="default-length-max",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_GTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="default-gte",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="default-lte",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_GT,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="default-gt",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LT,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="default-lt",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_RANGE,
            (
                (
                    ("quantity",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
                ),
                (
                    ("quantity",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="default-range",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_CHOICE,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="default-choice",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_REGEX,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="default-regex",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_TYPE,
            ((("count",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),),
            id="default-type",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_COUNT_MIN,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="default-count-min",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_COUNT_MAX,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="default-count-max",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_NESTED_PATH,
            ((("user", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="default-nested-path",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_ARRAY_INDEX,
            ((("tags", 0), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="default-array-index",
        ),
        pytest.param(
            _SYMFONY_API_NOT_BLANK,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="api-not-blank",
        ),
        pytest.param(
            _SYMFONY_API_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="api-email",
        ),
        pytest.param(
            _SYMFONY_API_LENGTH_MIN,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="api-length-min",
        ),
        pytest.param(
            _SYMFONY_API_GTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="api-gte",
        ),
        pytest.param(
            _SYMFONY_API_CHOICE,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="api-choice",
        ),
        pytest.param(
            _SYMFONY_API_REGEX,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="api-regex",
        ),
        pytest.param(
            [_violation("rank", _SYMFONY_CHOICE_CODE, value="0", choices="1, 2, 3")],
            ((("rank",), ObservationKind.ENUM, EnumPayload(values=("1", "2", "3"))),),
            id="choice-with-unquoted-integer-values",
        ),
    ],
)
def test_symfony_parser_parse(make_operation, body, expected, case_factory):
    operation = make_operation(method="post", path="/api/users")
    actual = SymfonyParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_symfony_parser_default_multi_field(make_operation, case_factory):
    observations = SymfonyParser().parse(
        operation=make_operation(), body=_SYMFONY_DEFAULT_MULTI_FIELD, case=case_factory()
    )
    assert _symfony_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
        ]
    )


def test_symfony_parser_api_platform_multi_field(make_operation, case_factory):
    observations = parse_observations(SymfonyParser(), _SYMFONY_API_MULTI_FIELD, make_operation, case_factory)
    assert _symfony_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
            (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),
        ]
    )


def test_symfony_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(SymfonyParser(), {"detail": "nope"}, make_operation, case_factory) == ()


def test_symfony_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = SymfonyParser().parse(
        operation=make_operation(method="get", path="/api/users"),
        body=_SYMFONY_DEFAULT_EMAIL,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


@pytest.mark.parametrize(
    "violation",
    [
        _violation("name", "11111111-2222-3333-4444-555555555555"),
        _violation("name", _SYMFONY_LENGTH_MIN_CODE),
        _violation("name", _SYMFONY_LENGTH_MIN_CODE, limit="abc"),
        _violation("name", _SYMFONY_GTE_CODE),
        _violation("name", _SYMFONY_GTE_CODE, compared_value="abc"),
        _violation("name", _SYMFONY_RANGE_CODE),
        _violation("name", _SYMFONY_RANGE_CODE, min="1"),
        _violation("name", _SYMFONY_RANGE_CODE, min="abc", max="100"),
        _violation("name", _SYMFONY_CHOICE_CODE),
        _violation("name", _SYMFONY_REGEX_CODE),
        _violation("name", _SYMFONY_TYPE_CODE),
        {**_violation("name", _SYMFONY_LENGTH_MIN_CODE, limit="3"), "parameters": "not a dict"},
        _violation("", _SYMFONY_NOT_BLANK_CODE),
        {"propertyPath": "name", "message": "no code field"},
    ],
    ids=[
        "unknown-code",
        "length-without-limit",
        "length-non-integer-limit",
        "gte-without-compared-value",
        "gte-non-numeric-compared-value",
        "range-without-min-max",
        "range-only-min",
        "range-non-numeric-min",
        "choice-without-choices-param",
        "regex-without-pattern-param",
        "type-without-type-param",
        "non-dict-parameters",
        "empty-property-path",
        "no-code-or-type-key",
    ],
)
def test_symfony_parser_drops_malformed_violation(make_operation, violation, case_factory):
    body = [violation, _violation("seed", _SYMFONY_EMAIL_CODE)]
    actual = parse_observations(SymfonyParser(), body, make_operation, case_factory)
    actual_paths = tuple(o.parameter_path for o in actual)
    assert actual_paths == (("seed",),)


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
        ZodParser(),
    ],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _SYMFONY_ACCEPTED_BODIES)
def test_other_parsers_reject_symfony_bodies(parser, body):
    assert parser.can_parse(body=body) is False
