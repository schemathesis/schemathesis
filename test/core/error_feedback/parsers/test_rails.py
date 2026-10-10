from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
    _classify_message,
    _split_legacy_message,
    _walk_legacy,
    _walk_modern,
)
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.parameters import ParameterLocation

_RAILS_PRESENCE_REQUIRED_MODERN = {
    "email": ["can't be blank", "is invalid"],
    "name": ["can't be blank", "is too short (minimum is 2 characters)"],
    "age": ["is not a number"],
    "role": ["is not included in the list"],
    "password": ["is too short (minimum is 8 characters)"],
    "terms_accepted": ["must be accepted"],
}


_RAILS_PRESENCE_REQUIRED_LEGACY = {
    "errors": [
        "Email can't be blank",
        "Email is invalid",
        "Name can't be blank",
        "Name is too short (minimum is 2 characters)",
        "Age is not a number",
        "Role is not included in the list",
        "Password is too short (minimum is 8 characters)",
        "Terms accepted must be accepted",
    ],
}


_RAILS_LENGTH_TOO_SHORT_MODERN = {"name": ["is too short (minimum is 2 characters)"]}


_RAILS_LENGTH_TOO_SHORT_LEGACY = {"errors": ["Name is too short (minimum is 2 characters)"]}


_RAILS_LENGTH_TOO_LONG_MODERN = {"name": ["is too long (maximum is 50 characters)"]}


_RAILS_LENGTH_TOO_LONG_LEGACY = {"errors": ["Name is too long (maximum is 50 characters)"]}


_RAILS_NUMERICALITY_BELOW_MIN_MODERN = {"age": ["must be greater than or equal to 0"]}


_RAILS_NUMERICALITY_BELOW_MIN_LEGACY = {"errors": ["Age must be greater than or equal to 0"]}


_RAILS_NUMERICALITY_ABOVE_MAX_MODERN = {"age": ["must be less than 130"]}


_RAILS_NUMERICALITY_ABOVE_MAX_LEGACY = {"errors": ["Age must be less than 130"]}


_RAILS_MULTI_FIELD_VIOLATIONS_MODERN = {
    "email": ["is invalid"],
    "name": ["is too short (minimum is 2 characters)"],
    "age": ["is not a number"],
    "role": ["is not included in the list"],
    "reserved_handle": ["is reserved"],
    "password_confirmation": ["doesn't match Password"],
    "password": ["is too short (minimum is 8 characters)"],
    "terms_accepted": ["must be accepted"],
}


_RAILS_MULTI_FIELD_VIOLATIONS_LEGACY = {
    "errors": [
        "Email is invalid",
        "Name is too short (minimum is 2 characters)",
        "Age is not a number",
        "Role is not included in the list",
        "Reserved handle is reserved",
        "Password confirmation doesn't match Password",
        "Password is too short (minimum is 8 characters)",
        "Terms accepted must be accepted",
    ],
}


_RAILS_NESTED_ATTRIBUTES_MODERN = {
    "address.street": ["can't be blank"],
    "address.city": ["can't be blank"],
    "address.zipcode": ["can't be blank", "is invalid"],
}


_RAILS_LENGTH_WRONG_EXACT_MODERN = {"exact_code": ["is the wrong length (should be 6 characters)"]}


_RAILS_LENGTH_ARRAY_TOO_LONG_MODERN = {"tag_list": ["is too long (maximum is 5 characters)"]}


_RAILS_NUMERICALITY_NOT_INTEGER_MODERN = {"age": ["must be an integer"]}


def _wrap_rails_modern(body: dict) -> dict:
    return {"errors": body}


_RAILS_ACCEPTED_BODIES = [
    pytest.param(_RAILS_PRESENCE_REQUIRED_MODERN, id="presence-modern"),
    pytest.param(_RAILS_PRESENCE_REQUIRED_LEGACY, id="presence-legacy"),
    pytest.param(_wrap_rails_modern(_RAILS_PRESENCE_REQUIRED_MODERN), id="presence-wrapped"),
    pytest.param(_RAILS_LENGTH_TOO_SHORT_MODERN, id="length-too-short"),
    pytest.param(_RAILS_LENGTH_TOO_LONG_MODERN, id="length-too-long"),
    pytest.param(_RAILS_LENGTH_WRONG_EXACT_MODERN, id="length-wrong-exact"),
    pytest.param(_RAILS_LENGTH_ARRAY_TOO_LONG_MODERN, id="length-array"),
    pytest.param(_RAILS_NUMERICALITY_BELOW_MIN_MODERN, id="numericality-min"),
    pytest.param(_RAILS_NUMERICALITY_ABOVE_MAX_MODERN, id="numericality-max"),
    pytest.param(_RAILS_NUMERICALITY_NOT_INTEGER_MODERN, id="numericality-not-integer"),
    pytest.param(_RAILS_MULTI_FIELD_VIOLATIONS_MODERN, id="multi-field-modern"),
    pytest.param(_RAILS_MULTI_FIELD_VIOLATIONS_LEGACY, id="multi-field-legacy"),
    pytest.param(_RAILS_NESTED_ATTRIBUTES_MODERN, id="nested-attributes"),
    pytest.param({"email": ["can't be blank"], "name": []}, id="empty-sibling-list"),
    pytest.param({"role": ["is not included in the list"]}, id="vocab-inclusion-only"),
]


@pytest.mark.parametrize("body", _RAILS_ACCEPTED_BODIES)
def test_rails_parser_can_parse_recognises_envelope(body):
    assert RailsParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        ["top-level-list"],
        {"detail": "single-message"},
        {"x": 5},
        {"x": True},
        {"name": ["This field is required."]},
        {"address": {"zipcode": ["This field is required."]}},
        {
            "message": "The given data was invalid.",
            "errors": {"email": ["The email must be a valid email address."]},
        },
        {
            "type": "...",
            "title": "One or more validation errors occurred.",
            "status": 400,
            "errors": {"Email": ["The Email field is required."]},
        },
        {"messages": ["email - must not be null"]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required", "type": "value_error.missing"}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "top-level-list-of-strings-without-rails-vocab",
        "detail-only",
        "scalar-int-leaf",
        "scalar-bool-leaf",
        "drf-flat",
        "drf-nested",
        "laravel",
        "aspnet-problemdetails",
        "spring-messages",
        "pydantic-detail",
    ],
)
def test_rails_parser_can_parse_rejects_non_rails_bodies(body):
    assert RailsParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Email can't be blank", ("email", "can't be blank")),
        ("Name is too short (minimum is 2 characters)", ("name", "is too short (minimum is 2 characters)")),
        ("Terms accepted must be accepted", ("terms_accepted", "must be accepted")),
        ("Password confirmation doesn't match Password", ("password_confirmation", "doesn't match Password")),
        ("Reserved handle is reserved", ("reserved_handle", "is reserved")),
        ("Age is not a number", ("age", "is not a number")),
        ("Age must be greater than or equal to 0", ("age", "must be greater than or equal to 0")),
    ],
    ids=[
        "single-token-blank",
        "single-token-size",
        "two-token-acceptance",
        "two-token-confirmation",
        "two-token-exclusion",
        "single-token-numericality-prose",
        "single-token-numericality-bound",
    ],
)
def test_rails_parser_split_legacy_message(line, expected):
    assert _split_legacy_message(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "nothing recognisable",  # no lead token
        "is invalid",  # leads with a token, no humanised attribute prefix
        "must be accepted",  # same — bare phrasing
    ],
)
def test_rails_parser_split_legacy_message_returns_none(line):
    assert _split_legacy_message(line) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            {"email": ["can't be blank"], "base": ["something cross-field"]},
            [(("email",), "can't be blank")],
            id="skips-base-key",
        ),
        pytest.param(
            {"address.street": ["can't be blank"]},
            [(("address", "street"), "can't be blank")],
            id="splits-dotted-key",
        ),
        pytest.param(
            {"email": ["can't be blank", ""]},
            [(("email",), "can't be blank")],
            id="skips-empty-string-message",
        ),
        pytest.param(
            {1: ["ignored"], "email": ["can't be blank"]},
            [(("email",), "can't be blank")],
            id="skips-non-string-key",
        ),
        pytest.param(
            {"email": "not-a-list"},
            [],
            id="skips-non-list-value",
        ),
    ],
)
def test_rails_parser_walk_modern(body, expected):
    assert list(_walk_modern(body)) == expected


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        pytest.param(
            ["Base must not violate constraint", "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-base-message",
        ),
        pytest.param(
            ["nothing recognisable", "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-line-without-lead-token",
        ),
        pytest.param(
            ["", "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-empty-string-line",
        ),
        pytest.param(
            [None, 42, "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-non-string-items",
        ),
    ],
)
def test_rails_parser_walk_legacy(messages, expected):
    assert list(_walk_legacy(messages)) == expected


@pytest.mark.parametrize(
    ("message", "kind", "payload"),
    [
        ("can't be blank", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("can't be empty", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("must be filled", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("must exist", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("is not a number", ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),
        ("must be an integer", ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
    ],
    ids=lambda v: getattr(v, "name", str(v))[:30],
)
def test_rails_parser_classifier_literals(message, kind, payload):
    assert _classify_message(message) == (kind, payload)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("is too short (minimum is 1 character)", SizeBoundPayload(min=1, max=None), id="min-singular"),
        pytest.param("is too short (minimum is 2 characters)", SizeBoundPayload(min=2, max=None), id="min-plural"),
        pytest.param("is too long (maximum is 1 character)", SizeBoundPayload(min=None, max=1), id="max-singular"),
        pytest.param("is too long (maximum is 50 characters)", SizeBoundPayload(min=None, max=50), id="max-plural"),
        pytest.param(
            "is the wrong length (should be 6 characters)",
            SizeBoundPayload(min=6, max=6),
            id="exact",
        ),
    ],
)
def test_rails_parser_classifier_size_bound(message, expected):
    kind, payload = _classify_message(message)
    assert kind is ObservationKind.SIZE_BOUND
    assert payload == expected


@pytest.mark.parametrize(
    ("message", "bound", "direction", "exclusive"),
    [
        ("must be greater than 0", 0.0, BoundDirection.MIN, True),
        ("must be greater than 0.0", 0.0, BoundDirection.MIN, True),
        ("must be greater than or equal to 0", 0.0, BoundDirection.MIN, False),
        ("must be less than 130", 130.0, BoundDirection.MAX, True),
        ("must be less than or equal to 100", 100.0, BoundDirection.MAX, False),
        ("must be greater than -5", -5.0, BoundDirection.MIN, True),
    ],
    ids=lambda v: str(v)[:30],
)
def test_rails_parser_classifier_numeric_bound(message, bound, direction, exclusive):
    kind, payload = _classify_message(message)
    assert kind is ObservationKind.NUMERIC_BOUND
    assert payload == NumericBoundPayload(bound=bound, direction=direction, exclusive=exclusive)


@pytest.mark.parametrize(
    "message",
    [
        "is invalid",
        "must be accepted",
        "is reserved",
        "is not included in the list",
        "has already been taken",
        "must be even",
        "must be odd",
        "must be other than 5",
        "doesn't match Password",
        "is too long",
        "completely custom application message",
    ],
)
def test_rails_parser_classifier_drops_unactionable_messages(message):
    assert _classify_message(message) is None


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_observations"),
    [
        pytest.param(
            "post",
            "/api/users",
            {"email": ["can't be blank"], "age": ["must be greater than or equal to 0"]},
            (
                ("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="modern-shape",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"errors": ["Email can't be blank", "Name is too short (minimum is 2 characters)"]},
            (
                ("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=2, max=None),
                ),
            ),
            id="legacy-shape",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"errors": {"email": ["can't be blank"]}},
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="ar-wrapped",
        ),
        pytest.param(
            "post",
            "/api/users",
            {
                "address.street": ["can't be blank"],
                "address.zipcode": ["can't be blank", "is invalid"],
            },
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("address", "street"),
                    ObservationKind.MUST_NOT_BE_BLANK,
                    None,
                ),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("address", "zipcode"),
                    ObservationKind.MUST_NOT_BE_BLANK,
                    None,
                ),
            ),
            id="nested-dotted-path-drops-is-invalid",
        ),
        pytest.param(
            "get",
            "/api/users",
            {"limit": ["must be greater than or equal to 0"]},
            (
                (
                    "GET /api/users",
                    ParameterLocation.QUERY,
                    ("limit",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="get-routes-to-query-location",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"email": ["something custom the application says"]},
            (),
            id="unrecognised-message-yields-empty",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"base": ["something cross-field"]},
            (),
            id="base-key-yields-empty",
        ),
    ],
)
def test_rails_parser_parse(make_operation, method, path, body, expected_observations, case_factory):
    operation = make_operation(method=method, path=path)
    actual = RailsParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.operation_label, o.location, o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected_observations


def _rails_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind.value, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("modern", "legacy"),
    [
        pytest.param(_RAILS_PRESENCE_REQUIRED_MODERN, _RAILS_PRESENCE_REQUIRED_LEGACY, id="presence_required"),
        pytest.param(_RAILS_LENGTH_TOO_SHORT_MODERN, _RAILS_LENGTH_TOO_SHORT_LEGACY, id="length_too_short"),
        pytest.param(_RAILS_LENGTH_TOO_LONG_MODERN, _RAILS_LENGTH_TOO_LONG_LEGACY, id="length_too_long"),
        pytest.param(
            _RAILS_NUMERICALITY_BELOW_MIN_MODERN,
            _RAILS_NUMERICALITY_BELOW_MIN_LEGACY,
            id="numericality_below_min",
        ),
        pytest.param(
            _RAILS_NUMERICALITY_ABOVE_MAX_MODERN,
            _RAILS_NUMERICALITY_ABOVE_MAX_LEGACY,
            id="numericality_above_max",
        ),
        pytest.param(
            _RAILS_MULTI_FIELD_VIOLATIONS_MODERN,
            _RAILS_MULTI_FIELD_VIOLATIONS_LEGACY,
            id="multi_field_violations",
        ),
    ],
)
def test_rails_parser_envelope_shapes_agree_on_observations(make_operation, modern, legacy, case_factory):
    operation = make_operation()
    parser = RailsParser()
    modern_signatures = _rails_signatures(parser.parse(operation=operation, body=modern, case=case_factory()))
    legacy_signatures = _rails_signatures(parser.parse(operation=operation, body=legacy, case=case_factory()))
    wrapped_signatures = _rails_signatures(
        parser.parse(operation=operation, body=_wrap_rails_modern(modern), case=case_factory())
    )
    assert modern_signatures == legacy_signatures == wrapped_signatures


@pytest.mark.parametrize(
    "parser",
    [PydanticParser(), SpringParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _RAILS_ACCEPTED_BODIES)
def test_other_parsers_reject_rails_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_rails_outranks_drf_when_both_claim_a_body():
    body = {"email": ["can't be blank"], "name": ["is too short (minimum is 2 characters)"]}
    assert RailsParser().can_parse(body=body) is True
    assert DRFParser().can_parse(body=body) is True
    assert RailsParser.priority > DRFParser.priority
