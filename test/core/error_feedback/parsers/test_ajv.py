from __future__ import annotations

import pytest

from schemathesis.core import NOT_SET
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
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
)
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


def _ajv_array_envelope(*errors: dict) -> dict:
    return {"errors": list(errors)}


def _fastify_envelope(message: str) -> dict:
    return {
        "statusCode": 400,
        "code": "FST_ERR_VALIDATION",
        "error": "Bad Request",
        "message": message,
    }


_AJV_FORMAT_EMAIL = _ajv_array_envelope(
    {
        "instancePath": "/email",
        "schemaPath": "#/properties/email/format",
        "keyword": "format",
        "params": {"format": "email"},
        "message": 'must match format "email"',
    }
)


_AJV_FORMAT_URI = _ajv_array_envelope(
    {
        "instancePath": "/url",
        "schemaPath": "#/properties/url/format",
        "keyword": "format",
        "params": {"format": "uri"},
        "message": 'must match format "uri"',
    }
)


_AJV_FORMAT_UUID = _ajv_array_envelope(
    {
        "instancePath": "/token",
        "schemaPath": "#/properties/token/format",
        "keyword": "format",
        "params": {"format": "uuid"},
        "message": 'must match format "uuid"',
    }
)


_AJV_FORMAT_DATETIME = _ajv_array_envelope(
    {
        "instancePath": "/when",
        "schemaPath": "#/properties/when/format",
        "keyword": "format",
        "params": {"format": "date-time"},
        "message": 'must match format "date-time"',
    }
)


_AJV_MIN_LENGTH = _ajv_array_envelope(
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/minLength",
        "keyword": "minLength",
        "params": {"limit": 3},
        "message": "must NOT have fewer than 3 characters",
    }
)


_AJV_MAX_LENGTH = _ajv_array_envelope(
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/maxLength",
        "keyword": "maxLength",
        "params": {"limit": 20},
        "message": "must NOT have more than 20 characters",
    }
)


_AJV_MIN_ITEMS = _ajv_array_envelope(
    {
        "instancePath": "/tags",
        "schemaPath": "#/properties/tags/minItems",
        "keyword": "minItems",
        "params": {"limit": 1},
        "message": "must NOT have fewer than 1 items",
    }
)


_AJV_MAX_ITEMS = _ajv_array_envelope(
    {
        "instancePath": "/tags",
        "schemaPath": "#/properties/tags/maxItems",
        "keyword": "maxItems",
        "params": {"limit": 5},
        "message": "must NOT have more than 5 items",
    }
)


_AJV_MINIMUM = _ajv_array_envelope(
    {
        "instancePath": "/age",
        "schemaPath": "#/properties/age/minimum",
        "keyword": "minimum",
        "params": {"comparison": ">=", "limit": 0},
        "message": "must be >= 0",
    }
)


_AJV_MAXIMUM = _ajv_array_envelope(
    {
        "instancePath": "/age",
        "schemaPath": "#/properties/age/maximum",
        "keyword": "maximum",
        "params": {"comparison": "<=", "limit": 130},
        "message": "must be <= 130",
    }
)


_AJV_EXCLUSIVE_MINIMUM = _ajv_array_envelope(
    {
        "instancePath": "/score",
        "schemaPath": "#/properties/score/exclusiveMinimum",
        "keyword": "exclusiveMinimum",
        "params": {"comparison": ">", "limit": 0},
        "message": "must be > 0",
    }
)


_AJV_EXCLUSIVE_MAXIMUM = _ajv_array_envelope(
    {
        "instancePath": "/score",
        "schemaPath": "#/properties/score/exclusiveMaximum",
        "keyword": "exclusiveMaximum",
        "params": {"comparison": "<", "limit": 100},
        "message": "must be < 100",
    }
)


_AJV_PATTERN = _ajv_array_envelope(
    {
        "instancePath": "/code",
        "schemaPath": "#/properties/code/pattern",
        "keyword": "pattern",
        "params": {"pattern": "^[A-Z]{3}$"},
        "message": 'must match pattern "^[A-Z]{3}$"',
    }
)


_AJV_ENUM = _ajv_array_envelope(
    {
        "instancePath": "/role",
        "schemaPath": "#/properties/role/enum",
        "keyword": "enum",
        "params": {"allowedValues": ["admin", "user", "guest"]},
        "message": "must be equal to one of the allowed values",
    }
)


_AJV_TYPE = _ajv_array_envelope(
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/type",
        "keyword": "type",
        "params": {"type": "string"},
        "message": "must be string",
    }
)


_AJV_ROOT_TYPE_OBJECT = _ajv_array_envelope(
    {
        "instancePath": "",
        "schemaPath": "#/type",
        "keyword": "type",
        "params": {"type": "object"},
        "message": "must be object",
    }
)


_AJV_ADDITIONAL_PROPERTIES = _ajv_array_envelope(
    {
        "instancePath": "",
        "schemaPath": "#/additionalProperties",
        "keyword": "additionalProperties",
        "params": {"additionalProperty": "unknown"},
        "message": "must NOT have additional properties",
    }
)


_AJV_REQUIRED_ROOT = _ajv_array_envelope(
    {
        "instancePath": "",
        "schemaPath": "#/required",
        "keyword": "required",
        "params": {"missingProperty": "email"},
        "message": "must have required property 'email'",
    }
)


_AJV_REQUIRED_NESTED = _ajv_array_envelope(
    {
        "instancePath": "/user",
        "schemaPath": "#/properties/user/required",
        "keyword": "required",
        "params": {"missingProperty": "email"},
        "message": "must have required property 'email'",
    }
)


_AJV_NESTED_FORMAT = _ajv_array_envelope(
    {
        "instancePath": "/user/email",
        "schemaPath": "#/properties/user/properties/email/format",
        "keyword": "format",
        "params": {"format": "email"},
        "message": 'must match format "email"',
    }
)


_AJV_ARRAY_ELEMENT_TYPE = _ajv_array_envelope(
    {
        "instancePath": "/tags/0",
        "schemaPath": "#/properties/tags/items/type",
        "keyword": "type",
        "params": {"type": "string"},
        "message": "must be string",
    }
)


_AJV_LEGACY_DATAPATH = {
    "errors": [
        {
            "dataPath": ".email",
            "schemaPath": "#/properties/email/format",
            "keyword": "format",
            "params": {"format": "email"},
            "message": 'should match format "email"',
        }
    ]
}


_AJV_MULTI_FIELD = _ajv_array_envelope(
    {
        "instancePath": "/email",
        "schemaPath": "#/properties/email/format",
        "keyword": "format",
        "params": {"format": "email"},
        "message": 'must match format "email"',
    },
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/minLength",
        "keyword": "minLength",
        "params": {"limit": 3},
        "message": "must NOT have fewer than 3 characters",
    },
    {
        "instancePath": "/age",
        "schemaPath": "#/properties/age/minimum",
        "keyword": "minimum",
        "params": {"comparison": ">=", "limit": 0},
        "message": "must be >= 0",
    },
)


_FASTIFY_FORMAT = _fastify_envelope('body/email must match format "email"')


_FASTIFY_MIN_LENGTH = _fastify_envelope("body/username must NOT have fewer than 3 characters")


_FASTIFY_MAX_LENGTH = _fastify_envelope("body/username must NOT have more than 20 characters")


_FASTIFY_MIN_ITEMS = _fastify_envelope("body/tags must NOT have fewer than 1 items")


_FASTIFY_MAX_ITEMS = _fastify_envelope("body/tags must NOT have more than 5 items")


_FASTIFY_MINIMUM = _fastify_envelope("body/age must be >= 0")


_FASTIFY_MAXIMUM = _fastify_envelope("body/age must be <= 130")


_FASTIFY_EXCLUSIVE_MINIMUM = _fastify_envelope("body/score must be > 0")


_FASTIFY_EXCLUSIVE_MAXIMUM = _fastify_envelope("body/score must be < 100")


_FASTIFY_PATTERN = _fastify_envelope('body/code must match pattern "^[A-Z]{3}$"')


_FASTIFY_REQUIRED = _fastify_envelope("body must have required property 'email'")


_FASTIFY_REQUIRED_NESTED = _fastify_envelope("body/user must have required property 'email'")


_FASTIFY_NESTED_FORMAT = _fastify_envelope('body/user/email must match format "email"')


_FASTIFY_ENUM = _fastify_envelope("body/role must be equal to one of the allowed values")


_FASTIFY_ADDITIONAL_PROPERTIES = _fastify_envelope("body must NOT have additional properties")


_FASTIFY_MULTI_FIELD = _fastify_envelope(
    'body/email must match format "email", body/username must NOT have fewer than 3 characters, '
    "body/age must be >= 0, body/role must be equal to one of the allowed values, "
    "body/tags must NOT have fewer than 1 items"
)


_AJV_ACCEPTED_BODIES = [
    pytest.param(_AJV_FORMAT_EMAIL, id="ajv-format-email"),
    pytest.param(_AJV_FORMAT_URI, id="ajv-format-uri"),
    pytest.param(_AJV_FORMAT_UUID, id="ajv-format-uuid"),
    pytest.param(_AJV_FORMAT_DATETIME, id="ajv-format-datetime"),
    pytest.param(_AJV_MIN_LENGTH, id="ajv-min-length"),
    pytest.param(_AJV_MAX_LENGTH, id="ajv-max-length"),
    pytest.param(_AJV_MIN_ITEMS, id="ajv-min-items"),
    pytest.param(_AJV_MAX_ITEMS, id="ajv-max-items"),
    pytest.param(_AJV_MINIMUM, id="ajv-minimum"),
    pytest.param(_AJV_MAXIMUM, id="ajv-maximum"),
    pytest.param(_AJV_EXCLUSIVE_MINIMUM, id="ajv-exclusive-minimum"),
    pytest.param(_AJV_EXCLUSIVE_MAXIMUM, id="ajv-exclusive-maximum"),
    pytest.param(_AJV_PATTERN, id="ajv-pattern"),
    pytest.param(_AJV_ENUM, id="ajv-enum"),
    pytest.param(_AJV_TYPE, id="ajv-type"),
    pytest.param(_AJV_ROOT_TYPE_OBJECT, id="ajv-root-type-object"),
    pytest.param(_AJV_ADDITIONAL_PROPERTIES, id="ajv-additional-properties"),
    pytest.param(_AJV_REQUIRED_ROOT, id="ajv-required-root"),
    pytest.param(_AJV_REQUIRED_NESTED, id="ajv-required-nested"),
    pytest.param(_AJV_NESTED_FORMAT, id="ajv-nested-format"),
    pytest.param(_AJV_ARRAY_ELEMENT_TYPE, id="ajv-array-element-type"),
    pytest.param(_AJV_LEGACY_DATAPATH, id="ajv-legacy-datapath"),
    pytest.param(_AJV_MULTI_FIELD, id="ajv-multi-field"),
    pytest.param(_FASTIFY_FORMAT, id="fastify-format"),
    pytest.param(_FASTIFY_MIN_LENGTH, id="fastify-min-length"),
    pytest.param(_FASTIFY_MAX_LENGTH, id="fastify-max-length"),
    pytest.param(_FASTIFY_MIN_ITEMS, id="fastify-min-items"),
    pytest.param(_FASTIFY_MAX_ITEMS, id="fastify-max-items"),
    pytest.param(_FASTIFY_MINIMUM, id="fastify-minimum"),
    pytest.param(_FASTIFY_MAXIMUM, id="fastify-maximum"),
    pytest.param(_FASTIFY_EXCLUSIVE_MINIMUM, id="fastify-exclusive-minimum"),
    pytest.param(_FASTIFY_EXCLUSIVE_MAXIMUM, id="fastify-exclusive-maximum"),
    pytest.param(_FASTIFY_PATTERN, id="fastify-pattern"),
    pytest.param(_FASTIFY_REQUIRED, id="fastify-required"),
    pytest.param(_FASTIFY_REQUIRED_NESTED, id="fastify-required-nested"),
    pytest.param(_FASTIFY_NESTED_FORMAT, id="fastify-nested-format"),
    pytest.param(_FASTIFY_ENUM, id="fastify-enum"),
    pytest.param(_FASTIFY_ADDITIONAL_PROPERTIES, id="fastify-additional-properties"),
    pytest.param(_FASTIFY_MULTI_FIELD, id="fastify-multi-field"),
]


@pytest.mark.parametrize("body", _AJV_ACCEPTED_BODIES)
def test_ajv_parser_can_parse_recognises_envelope(body):
    assert AjvParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"errors": []},
        {"errors": [{"code": "invalid_string", "path": ["email"]}]},
        {"errors": [{"keyword": "format"}]},
        {"errors": [{"instancePath": "/x"}]},
        {"statusCode": 500, "error": "Internal Server Error", "message": "Something went wrong"},
        {"statusCode": 400, "error": "Bad Request", "message": "Invalid token"},
        {"name": ["This field is required."]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "errors-empty-list",
        "zod-issue-shape",
        "ajv-issue-without-instancepath",
        "ajv-issue-without-keyword",
        "fastify-non-validation-status",
        "fastify-message-without-location-prefix",
        "drf",
        "pydantic",
    ],
)
def test_ajv_parser_can_parse_rejects_non_ajv_bodies(body):
    assert AjvParser().can_parse(body=body) is False


def _ajv_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _AJV_FORMAT_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="ajv-format-email",
        ),
        pytest.param(
            _AJV_FORMAT_URI,
            ((("url",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="ajv-format-uri",
        ),
        pytest.param(
            _AJV_FORMAT_UUID,
            ((("token",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="ajv-format-uuid",
        ),
        pytest.param(
            _AJV_FORMAT_DATETIME,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date-time")),),
            id="ajv-format-datetime",
        ),
        pytest.param(
            _AJV_MIN_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="ajv-min-length",
        ),
        pytest.param(
            _AJV_MAX_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="ajv-max-length",
        ),
        pytest.param(
            _AJV_MIN_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="ajv-min-items",
        ),
        pytest.param(
            _AJV_MAX_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="ajv-max-items",
        ),
        pytest.param(
            _AJV_MINIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="ajv-minimum",
        ),
        pytest.param(
            _AJV_MAXIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="ajv-maximum",
        ),
        pytest.param(
            _AJV_EXCLUSIVE_MINIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="ajv-exclusive-minimum",
        ),
        pytest.param(
            _AJV_EXCLUSIVE_MAXIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="ajv-exclusive-maximum",
        ),
        pytest.param(
            _AJV_PATTERN,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="ajv-pattern",
        ),
        pytest.param(
            _AJV_ENUM,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="ajv-enum",
        ),
        pytest.param(
            _AJV_TYPE,
            ((("username",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="ajv-type",
        ),
        pytest.param(_AJV_ADDITIONAL_PROPERTIES, (), id="ajv-additional-properties-dropped"),
        pytest.param(
            _AJV_REQUIRED_ROOT,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="ajv-required-root",
        ),
        pytest.param(
            _AJV_REQUIRED_NESTED,
            ((("user", "email"), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="ajv-required-nested",
        ),
        pytest.param(
            _AJV_NESTED_FORMAT,
            ((("user", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="ajv-nested-format",
        ),
        pytest.param(
            _AJV_ARRAY_ELEMENT_TYPE,
            ((("tags", 0), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="ajv-array-element-type",
        ),
        pytest.param(
            _AJV_LEGACY_DATAPATH,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="ajv-legacy-datapath",
        ),
        pytest.param(
            _FASTIFY_FORMAT,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="fastify-format",
        ),
        pytest.param(
            _FASTIFY_MIN_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="fastify-min-length",
        ),
        pytest.param(
            _FASTIFY_MAX_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="fastify-max-length",
        ),
        pytest.param(
            _FASTIFY_MIN_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="fastify-min-items",
        ),
        pytest.param(
            _FASTIFY_MAX_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="fastify-max-items",
        ),
        pytest.param(
            _FASTIFY_MINIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="fastify-minimum",
        ),
        pytest.param(
            _FASTIFY_MAXIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="fastify-maximum",
        ),
        pytest.param(
            _FASTIFY_EXCLUSIVE_MINIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="fastify-exclusive-minimum",
        ),
        pytest.param(
            _FASTIFY_EXCLUSIVE_MAXIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="fastify-exclusive-maximum",
        ),
        pytest.param(
            _FASTIFY_PATTERN,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="fastify-pattern",
        ),
        pytest.param(
            _FASTIFY_REQUIRED,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="fastify-required",
        ),
        pytest.param(
            _FASTIFY_REQUIRED_NESTED,
            ((("user", "email"), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="fastify-required-nested",
        ),
        pytest.param(
            _FASTIFY_NESTED_FORMAT,
            ((("user", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="fastify-nested-format",
        ),
        pytest.param(_FASTIFY_ENUM, (), id="fastify-enum-dropped"),
        pytest.param(_FASTIFY_ADDITIONAL_PROPERTIES, (), id="fastify-additional-properties-dropped"),
    ],
)
def test_ajv_parser_parse(make_operation, body, expected, case_factory):
    operation = make_operation(method="post", path="/api/users")
    actual = AjvParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


@pytest.mark.parametrize("case_body", [NOT_SET, None], ids=["omitted", "null"])
def test_ajv_parser_emits_body_required_for_root_type_error(make_operation, case_factory, case_body):
    operation = make_operation(method="post", path="/api/users")
    actual = AjvParser().parse(
        operation=operation,
        body=_AJV_ROOT_TYPE_OBJECT,
        case=case_factory(body=case_body),
    )
    assert tuple((o.parameter_path, o.kind, o.payload) for o in actual) == (
        ((), ObservationKind.MUST_NOT_BE_BLANK, None),
    )


def test_ajv_parser_drops_root_type_error_when_body_was_present(make_operation, case_factory):
    operation = make_operation(method="post", path="/api/users")
    actual = AjvParser().parse(
        operation=operation,
        body=_AJV_ROOT_TYPE_OBJECT,
        case=case_factory(body=[]),
    )
    assert actual == ()


@pytest.mark.parametrize(
    ("method", "expected"),
    [("post", (((), ObservationKind.MUST_NOT_BE_BLANK, None),)), ("get", ())],
    ids=["request-body", "query"],
)
def test_ajv_parser_root_type_error_only_applies_to_request_body(make_operation, case_factory, method, expected):
    operation = make_operation(method=method, path="/api/users")
    actual = AjvParser().parse(operation=operation, body=_AJV_ROOT_TYPE_OBJECT, case=case_factory(body=NOT_SET))
    assert tuple((o.parameter_path, o.kind, o.payload) for o in actual) == expected


def test_ajv_parser_parse_multi_field(make_operation, case_factory):
    observations = parse_observations(AjvParser(), _AJV_MULTI_FIELD, make_operation, case_factory)
    assert _ajv_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
        ]
    )


def test_ajv_parser_fastify_parse_multi_field_clauses(make_operation, case_factory):
    observations = parse_observations(AjvParser(), _FASTIFY_MULTI_FIELD, make_operation, case_factory)
    assert _ajv_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
            (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),
        ]
    )


def test_ajv_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(AjvParser(), {"detail": "nope"}, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    ("raw_path", "expected_path"),
    [
        (".user.email", ("user", "email")),
        ("[0]", (0,)),
        ("['weird key']", ("weird key",)),
        ("", ()),
    ],
    ids=["dotted", "array-index", "quoted-property", "empty"],
)
def test_ajv_parser_legacy_datapath_segments(make_operation, raw_path, expected_path, case_factory):
    body = {
        "errors": [
            {
                "dataPath": raw_path,
                "keyword": "format",
                "params": {"format": "email"},
                "message": 'must match format "email"',
            }
        ]
    }
    obs = parse_observations(AjvParser(), body, make_operation, case_factory)
    if expected_path:
        assert obs and obs[0].parameter_path == expected_path
    else:
        assert obs == ()


def test_ajv_parser_fastify_drops_message_without_clauses(make_operation, case_factory):
    body = _fastify_envelope("malformed")
    assert AjvParser().can_parse(body=body) is False
    assert parse_observations(AjvParser(), body, make_operation, case_factory) == ()


def test_ajv_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = AjvParser().parse(
        operation=make_operation(method="get", path="/api/users"),
        body=_AJV_FORMAT_EMAIL,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


def test_ajv_parser_fastify_query_prefix_routes_to_query_location(make_operation, case_factory):
    body = _fastify_envelope('query/email must match format "email"')
    obs = AjvParser().parse(
        operation=make_operation(method="post", path="/api/users"),
        body=body,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY and obs[0].parameter_path == ("email",)


@pytest.mark.parametrize(
    "issue",
    [
        {"keyword": "minimum", "instancePath": "/age", "params": {"limit": "abc"}, "message": "must be >= abc"},
        {"keyword": "minLength", "instancePath": "/x", "params": {"limit": True}, "message": "..."},
        {"keyword": "minLength", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "format", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "format", "instancePath": "/x", "params": {"format": 123}, "message": "..."},
        {"keyword": "pattern", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "enum", "instancePath": "/x", "params": {"allowedValues": []}, "message": "..."},
        {"keyword": "enum", "instancePath": "/x", "params": {"allowedValues": [1, 2]}, "message": "..."},
        {"keyword": "type", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "type", "instancePath": "/x", "params": {"type": ["string", "null"]}, "message": "..."},
        {"keyword": "required", "instancePath": "", "params": {}, "message": "..."},
        {"keyword": "required", "instancePath": "", "message": "..."},
        {"keyword": "completely_unknown", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "format", "instancePath": "", "params": {"format": "email"}, "message": "..."},
        {"keyword": "format", "instancePath": "/x", "message": "..."},
        {"keyword": "pattern", "instancePath": "/x", "message": "..."},
        {"keyword": "enum", "instancePath": "/x", "message": "..."},
        {"keyword": "type", "instancePath": "/x", "message": "..."},
        {"keyword": "minLength", "instancePath": "/x", "message": "..."},
        {"keyword": "minimum", "instancePath": "/x", "message": "..."},
    ],
    ids=[
        "non-numeric-limit",
        "bool-limit",
        "missing-limit",
        "missing-format",
        "non-string-format",
        "missing-pattern",
        "empty-enum",
        "non-string-enum",
        "missing-type",
        "list-type-with-null",
        "required-without-missingproperty",
        "required-without-params-key",
        "unknown-keyword",
        "non-required-keyword-with-empty-instance-path",
        "format-without-params-key",
        "pattern-without-params-key",
        "enum-without-params-key",
        "type-without-params-key",
        "size-without-params-key",
        "bound-without-params-key",
    ],
)
def test_ajv_parser_array_form_drops_malformed_issues(make_operation, issue, case_factory):
    body = {
        "errors": [
            issue,
            {
                "instancePath": "/seed",
                "keyword": "format",
                "params": {"format": "email"},
                "message": 'must match format "email"',
            },
        ]
    }
    actual = parse_observations(AjvParser(), body, make_operation, case_factory)
    actual_paths = tuple(o.parameter_path for o in actual)
    assert actual_paths == (("seed",),)


@pytest.mark.parametrize(
    "parser",
    [AspNetParser(), LaravelParser(), RailsParser(), PydanticParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _AJV_ACCEPTED_BODIES)
def test_other_parsers_reject_ajv_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_ajv_outranks_zod_when_array_signature_collides():
    # Bodies that carry both `keyword` and `code` could match either parser; priority picks AJV.
    mixed = {
        "errors": [
            {
                "instancePath": "/email",
                "keyword": "format",
                "code": "invalid_string",
                "path": ["email"],
                "params": {"format": "email"},
                "message": 'must match format "email"',
            }
        ]
    }
    assert AjvParser().can_parse(body=mixed) is True
    assert ZodParser().can_parse(body=mixed) is True
    assert AjvParser.priority > ZodParser.priority
