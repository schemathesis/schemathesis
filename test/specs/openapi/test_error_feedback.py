from __future__ import annotations

import json

import jsonschema_rs
import pytest

from schemathesis.core.error_feedback import (
    MAX_ENTRIES_PER_BUCKET,
    BoundDirection,
    EnumPayload,
    ErrorFeedbackStore,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.error_feedback.pipeline import FeedbackPipeline, _reset_pipeline_for_tests
from schemathesis.core.jsonschema import make_validator
from schemathesis.core.jsonschema.patterns import normalize_regex
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.meta import (
    CaseMetadata,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.specs.openapi._hypothesis import _body_required_per_feedback
from schemathesis.specs.openapi.definitions import OPENAPI_30, OPENAPI_31, SWAGGER_20
from schemathesis.specs.openapi.error_feedback import (
    AdditionalPropertiesAdjustment,
    EnumAdjustment,
    FormatAdjustment,
    NumericBoundAdjustment,
    PatternAdjustment,
    RequiredFieldAdjustment,
    SizeBoundAdjustment,
    TypeMismatchAdjustment,
    UnexpectedPropertyAdjustment,
    apply_adjustments,
)
from test.core.error_feedback.parsers.helpers import DRF_DETAIL_WRAPPED_BODY, SPRING_MESSAGES, drf_obs

# Schema Object meta-validators for each supported spec version. Adjustments must
# produce schemas that satisfy these so downstream Hypothesis draws against the
# spec meta-schema do not blow up — e.g. `required: []` violates the `minItems: 1`
# constraint on `required` in Swagger 2.0 and OpenAPI 3.0.
_SCHEMA_OBJECT_VALIDATORS: dict[str, jsonschema_rs.Validator] = {
    "2.0": make_validator(
        {"$ref": "#/definitions/schema", "definitions": SWAGGER_20["definitions"]},
        jsonschema_rs.Draft4Validator,
    ),
    "3.0": make_validator(
        {"$ref": "#/definitions/Schema", "definitions": OPENAPI_30["definitions"]},
        jsonschema_rs.Draft4Validator,
    ),
    "3.1": make_validator(
        {"$ref": "#/$defs/schema", "$defs": OPENAPI_31["$defs"]},
        jsonschema_rs.Draft202012Validator,
    ),
}


def _has_type_union(value: object) -> bool:
    """Detect OpenAPI 3.1 / JSON Schema 2020-12 type-union syntax (`type: [..., null]`)."""
    if isinstance(value, dict):
        if isinstance(value.get("type"), list):
            return True
        return any(_has_type_union(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_type_union(v) for v in value)
    return False


def _assert_valid_schema_object(input_schema: object, out: object, *, draft: str = "3.0") -> None:
    """An adjustment must not turn a valid Schema Object into an invalid one.

    The validator is auto-selected: schemas using `type: [..., null]` syntax are
    OpenAPI 3.1; anything else is checked against `draft` (default 3.0, stricter —
    catches `required: []` that 3.1 silently allows). Skipped when the input is not
    itself a valid Schema Object: a few cases deliberately feed malformed input to
    verify the adjustment is resilient, and the adjustment only owns its own changes.
    """
    if not isinstance(out, dict) or not isinstance(input_schema, dict):
        return
    effective_draft = "3.1" if _has_type_union(out) or _has_type_union(input_schema) else draft
    validator = _SCHEMA_OBJECT_VALIDATORS[effective_draft]
    if not validator.is_valid(input_schema):
        return
    try:
        validator.validate(out)
    except jsonschema_rs.ValidationError as exc:
        raise AssertionError(f"Adjustment output is not a valid Schema Object: {exc}") from exc


def _apply_body_adjustment(adjustment, schema, observations, case_factory):
    out = adjustment.apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=observations,
    )
    _assert_valid_schema_object(schema, out)
    return out


def test_spring_parser_locates_declared_query_parameters(ctx, case_factory):
    # Spring names the field without saying where it came from, so a declared query parameter
    # must not be mistaken for a body field.
    schema = ctx.openapi.load_schema(
        {
            "/api/stats": {
                "get": {
                    "parameters": [{"name": "month", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/api/stats"]["GET"]
    body = {"errors": [{"field": "month", "defaultMessage": "must be between 1 and 12"}]}
    obs = SpringParser().parse(operation=operation, body=body, case=case_factory(operation=operation))
    assert obs
    assert {o.location for o in obs} == {ParameterLocation.QUERY}


def _drf_query_operation(ctx, parameters):
    schema = ctx.openapi.load_schema(
        {"/api/audio": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}}
    )
    return schema["/api/audio"]["GET"]


@pytest.mark.parametrize(
    ("parameters", "expected_path"),
    [
        ([{"name": "tags", "in": "query", "schema": {"type": "string"}}], ("tags",)),
        (
            [
                {
                    "name": "detail",
                    "in": "query",
                    "style": "deepObject",
                    "schema": {"type": "object", "properties": {"tags": {"type": "string"}}},
                }
            ],
            ("detail", "tags"),
        ),
    ],
    ids=["detail-envelope", "declared-detail-parameter"],
)
def test_drf_parser_unwraps_detail_envelope_around_query_errors(ctx, case_factory, parameters, expected_path):
    operation = _drf_query_operation(ctx, parameters)
    assert DRFParser().parse(
        operation=operation, body=DRF_DETAIL_WRAPPED_BODY, case=case_factory(operation=operation)
    ) == (
        drf_obs(
            op="GET /api/audio",
            location=ParameterLocation.QUERY,
            path=expected_path,
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field may not be blank.",
        ),
    )


@pytest.mark.parametrize(
    ("body", "expected_paths"),
    [
        ({"detail": "Invalid input."}, []),
        ({"detail": {"tags": ["This field may not be blank."]}}, [("tags",)]),
    ],
    ids=["detail-message", "detail-envelope"],
)
def test_drf_parser_detail_message_is_not_an_envelope(ctx, case_factory, body, expected_paths):
    operation = _drf_query_operation(ctx, [{"name": "tags", "in": "query", "schema": {"type": "string"}}])
    observations = DRFParser().parse(operation=operation, body=body, case=case_factory(operation=operation))
    assert [observation.parameter_path for observation in observations] == expected_paths


def test_drf_detail_envelope_refines_declared_query_parameter(ctx, case_factory):
    operation = _drf_query_operation(ctx, [{"name": "tags", "in": "query", "schema": {"type": "string"}}])
    store = ErrorFeedbackStore()
    for observation in DRFParser().parse(
        operation=operation, body=DRF_DETAIL_WRAPPED_BODY, case=case_factory(operation=operation)
    ):
        store.record(observation)
    schema = operation.query.schema
    adjusted = apply_adjustments(operation=operation, location=ParameterLocation.QUERY, schema=schema, store=store)
    _assert_valid_schema_object(schema, adjusted)
    assert adjusted == {
        "type": "object",
        "properties": {"tags": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
        "required": ["tags"],
    }


def test_pipeline_dispatches_to_spring_parser_for_spring_shape(case_factory, response_factory):
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=SPRING_MESSAGES,
            content_type="application/json",
            status_code=400,
        )
    )
    obs = FeedbackPipeline.from_registry().parse(
        operation=case.operation,
        case=case,
        response=response,
    )
    assert [o.parameter_path for o in obs] == [("zipcode",), ("city",)]


def test_pipeline_skips_responses_with_no_known_deserializer(case_factory, response_factory):
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=b"\x00\x01\x02",
            content_type="application/octet-stream",
            status_code=400,
        )
    )
    assert (
        FeedbackPipeline.from_registry().parse(
            operation=case.operation,
            case=case,
            response=response,
        )
        == ()
    )


def test_pipeline_returns_empty_when_no_parser_matches(case_factory, response_factory):
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=b'{"random": "shape"}',
            content_type="application/json",
            status_code=400,
        )
    )
    assert (
        FeedbackPipeline.from_registry().parse(
            operation=case.operation,
            case=case,
            response=response,
        )
        == ()
    )


def _meta_for(mode: GenerationMode) -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description="",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
        ),
    )


def _drive_collector(
    *,
    case_factory,
    response_factory,
    status_code: int = 400,
    body: bytes = SPRING_MESSAGES,
    mode: GenerationMode = GenerationMode.POSITIVE,
    times: int = 1,
):
    _reset_pipeline_for_tests()
    case = case_factory(_meta=_meta_for(mode))
    response = Response.from_any(
        response_factory.requests(
            content=body,
            content_type="application/json",
            status_code=status_code,
        )
    )
    store = ErrorFeedbackStore()
    for _ in range(times):
        record_response(store=store, operation=case.operation, case=case, response=response)
    return store, case


def test_collector_records_observations_from_400(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
    )
    out = store.observations(operation_label=case.operation.label, location=ParameterLocation.BODY)
    assert sorted(o.parameter_path for o in out) == [("city",), ("zipcode",)]


@pytest.mark.parametrize("status_code", [200, 401, 403, 500, 503])
def test_collector_skips_non_4xx_and_auth_failures(status_code, case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=status_code,
    )
    out = store.observations(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
    )
    assert out == ()


def test_collector_skips_negative_mode_cases(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
        mode=GenerationMode.NEGATIVE,
    )
    out = store.observations(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
    )
    assert out == ()


def _build_observations(*paths: tuple[str | int, ...]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=p,
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="must not be blank",
        )
        for p in paths
    )


@pytest.mark.parametrize(
    "input_schema, paths, expected",
    [
        (
            {"type": "object", "properties": {"email": {"type": "string"}}},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {}},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"email": {"type": "string", "minLength": 5}}},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 5}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}},
            [("age",)],
            {
                "type": "object",
                "properties": {"age": {"type": "integer"}},
                "required": ["age"],
            },
        ),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {}},
                    {"type": "string"},
                ]
            },
            [("email",)],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 1}},
                        "required": ["email"],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (True, [("email",)], True),
        (
            {
                "type": ["object", "null"],
                "properties": {"email": {"type": ["string", "null"]}},
            },
            [("email",)],
            {
                "type": ["object", "null"],
                "properties": {"email": {"type": ["string", "null"], "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {
                "type": ["object", "null"],
                "properties": {"age": {"type": ["integer", "null"]}},
            },
            [("age",)],
            {
                "type": ["object", "null"],
                "properties": {"age": {"type": ["integer", "null"]}},
                "required": ["age"],
            },
        ),
        (
            {"properties": {"email": {"type": "string"}}, "required": "garbage"},
            [("email",)],
            {
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"email": True}},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"email": False}},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "string"},
            [("email",)],
            {"type": "string"},
        ),
        (
            {"oneOf": [{"type": "string"}, {"type": "number"}]},
            [("email",)],
            {"oneOf": [{"type": "string"}, {"type": "number"}]},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                    }
                },
            },
            [("contact", "email")],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 1}},
                        "required": ["email"],
                    }
                },
            },
        ),
        (
            {"type": "object", "properties": {}},
            [("address", "street")],
            {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "properties": {"street": {"type": "string", "minLength": 1}},
                        "required": ["street"],
                    }
                },
            },
        ),
        (
            {"type": "object", "properties": {}},
            [("address", "city", "zip")],
            {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "object",
                                "properties": {"zip": {"type": "string", "minLength": 1}},
                                "required": ["zip"],
                            }
                        },
                    }
                },
            },
        ),
        (
            {"type": "object", "properties": {}},
            [()],
            {"type": "object", "properties": {}},
        ),
        (
            {"type": "object", "properties": {}},
            [("foo", 0, "bar")],
            {"type": "object", "properties": {}},
        ),
        (
            {"type": "object", "properties": {}},
            [("foo", 0)],
            {"type": "object", "properties": {}},
        ),
    ],
    ids=[
        "string-property-bump-minlength",
        "absent-property-inject",
        "stronger-minlength-preserved",
        "integer-property-required-only",
        "oneof-applies-to-object-branch",
        "bool-schema-passthrough",
        "type-union-with-null-tightens-string",
        "type-union-with-null-preserves-non-string",
        "non-list-required-discarded",
        "boolean-true-leaf-replaced-with-default",
        "boolean-false-leaf-replaced-with-default",
        "non-object-root-no-targets-passthrough",
        "oneof-with-no-object-branches-no-targets-passthrough",
        "nested-path-tightens-and-marks-required",
        "nested-path-creates-missing-intermediate-object",
        "deep-path-synthesises-only-leaf-required",
        "empty-path-observation-skipped",
        "non-string-step-in-prefix-skipped",
        "non-string-leaf-skipped",
    ],
)
def test_required_field_adjustment_applies_correctly(input_schema, paths, expected, case_factory):
    assert (
        _apply_body_adjustment(RequiredFieldAdjustment(), input_schema, _build_observations(*paths), case_factory)
        == expected
    )


def test_required_field_adjustment_idempotent(case_factory):
    # `apply` mutates in place, so feed it the same dict twice and check
    # the second pass doesn't drift from the first.
    schema = {"type": "object", "properties": {}}
    obs = _build_observations(("email",))
    operation = case_factory().operation

    original = {**schema}
    RequiredFieldAdjustment().apply(
        operation=operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=obs,
    )
    _assert_valid_schema_object(original, schema)
    snapshot = {**schema, "properties": {**schema["properties"]}, "required": [*schema["required"]]}
    RequiredFieldAdjustment().apply(
        operation=operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=obs,
    )
    _assert_valid_schema_object(snapshot, schema)
    assert schema == snapshot


def test_apply_adjustments_returns_input_when_no_observations(case_factory):
    schema = {"type": "object", "properties": {}}
    store = ErrorFeedbackStore()
    out = apply_adjustments(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=schema,
        store=store,
    )
    _assert_valid_schema_object(schema, out)
    assert out is schema


def _build_size_bound_observations(
    *items: tuple[tuple[str | int, ...], int, int],
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.SIZE_BOUND,
            raw_message=f"size must be between {min_value} and {max_value}",
            payload=SizeBoundPayload(min=min_value, max=max_value),
        )
        for path, min_value, max_value in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"username": {"type": "string"}}},
            [(("username",), 0, 15)],
            {
                "type": "object",
                "properties": {"username": {"type": "string", "minLength": 0, "maxLength": 15}},
            },
        ),
        (
            {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}}}},
            [(("tags",), 1, 5)],
            {
                "type": "object",
                "properties": {
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                },
            },
        ),
        (
            {"type": "object", "properties": {"meta": {"type": "object"}}},
            [(("meta",), 1, 10)],
            {
                "type": "object",
                "properties": {"meta": {"type": "object", "minProperties": 1, "maxProperties": 10}},
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}},
            [(("age",), 0, 100)],
            {"type": "object", "properties": {"age": {"type": "integer"}}},
        ),
        (
            {
                "type": "object",
                "properties": {"username": {"type": "string", "minLength": 5, "maxLength": 50}},
            },
            [(("username",), 0, 15)],
            {
                "type": "object",
                "properties": {"username": {"type": "string", "minLength": 5, "maxLength": 15}},
            },
        ),
        (
            {"type": "object", "properties": {"username": {"type": ["string", "null"]}}},
            [(("username",), 0, 15)],
            {
                "type": "object",
                "properties": {"username": {"type": ["string", "null"], "minLength": 0, "maxLength": 15}},
            },
        ),
        (
            {"type": "object", "properties": {}},
            [(("username",), 0, 15)],
            {"type": "object", "properties": {}},
        ),
        (
            {"type": "string"},
            [(("username",), 0, 15)],
            {"type": "string"},
        ),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"username": {"type": "string"}}},
                    {"type": "string"},
                ]
            },
            [(("username",), 0, 15)],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"username": {"type": "string", "minLength": 0, "maxLength": 15}},
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                    }
                },
            },
            [(("contact", "email"), 5, 64)],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 5, "maxLength": 64}},
                    }
                },
            },
        ),
        (True, [(("username",), 0, 15)], True),
        (
            {"type": "object", "properties": {"username": {"type": "string"}}},
            [((), 0, 15)],
            {"type": "object", "properties": {"username": {"type": "string"}}},
        ),
        (
            {"type": "object", "properties": {"username": {"type": "string"}}},
            [((0,), 0, 15)],
            {"type": "object", "properties": {"username": {"type": "string"}}},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {"type": "object", "properties": "broken"},
                },
            },
            [(("contact", "email"), 5, 64)],
            {
                "type": "object",
                "properties": {
                    "contact": {"type": "object", "properties": "broken"},
                },
            },
        ),
    ],
    ids=[
        "string-property-applies-length-bounds",
        "array-property-applies-item-bounds",
        "object-property-applies-property-bounds",
        "integer-property-no-applicable-keyword",
        "tighter-server-bound-overrides-looser-existing",
        "type-union-with-null-applies-length-bounds",
        "absent-property-not-synthesised",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-applies-bounds",
        "bool-schema-passthrough",
        "empty-path-observation-skipped",
        "non-string-step-skipped",
        "non-dict-intermediate-properties-skipped",
    ],
)
def test_size_bound_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(
            SizeBoundAdjustment(), input_schema, _build_size_bound_observations(*items), case_factory
        )
        == expected
    )


def _build_format_observations(*items: tuple[tuple[str | int, ...], str]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.FORMAT,
            raw_message=f"must be a valid {name}",
            payload=FormatPayload(name=name),
        )
        for path, name in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"email": {"type": "string"}}},
            [(("email",), "email")],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "email"}},
            },
        ),
        (
            {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "uri"}},
            },
            [(("email",), "email")],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "uri"}},
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}},
            [(("age",), "email")],
            {"type": "object", "properties": {"age": {"type": "integer"}}},
        ),
        (
            {"type": "object", "properties": {"email": {"type": ["string", "null"]}}},
            [(("email",), "email")],
            {
                "type": "object",
                "properties": {"email": {"type": ["string", "null"], "format": "email"}},
            },
        ),
        (
            {"type": "object", "properties": {}},
            [(("email",), "email")],
            {"type": "object", "properties": {}},
        ),
        (True, [(("email",), "email")], True),
        ({"type": "string"}, [(("email",), "email")], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"email": {"type": "string"}}},
                    {"type": "string"},
                ]
            },
            [(("email",), "email")],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"email": {"type": "string", "format": "email"}},
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                    }
                },
            },
            [(("contact", "email"), "email")],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "format": "email"}},
                    }
                },
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}},
            [((), "email")],
            {"type": "object", "properties": {"x": {"type": "string"}}},
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}},
            [((0,), "email")],
            {"type": "object", "properties": {"x": {"type": "string"}}},
        ),
    ],
    ids=[
        "string-property-format-injected",
        "existing-format-preserved",
        "non-string-property-skipped",
        "type-union-with-null-format-injected",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-format-injected",
        "empty-path-observation-skipped",
        "non-string-step-skipped",
    ],
)
def test_format_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(FormatAdjustment(), input_schema, _build_format_observations(*items), case_factory)
        == expected
    )


def _build_numeric_bound_observations(
    *items: tuple[tuple[str | int, ...], float, BoundDirection, bool],
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message=f"must be {direction.value} {bound}",
            payload=NumericBoundPayload(bound=bound, direction=direction, exclusive=exclusive),
        )
        for path, bound, direction, exclusive in items
    )


@pytest.fixture
def openapi_31_case_factory(openapi_31):
    def factory():
        return openapi_31["/users"]["GET"].Case(method="GET", media_type="application/json")

    return factory


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0}}},
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}},
            [(("score",), 100.0, BoundDirection.MAX, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "maximum": 100, "exclusiveMaximum": True}},
            },
        ),
        (
            {"type": "object", "properties": {"price": {"type": "number"}}},
            [(("price",), 0.5, BoundDirection.MIN, True)],
            {
                "type": "object",
                "properties": {"price": {"type": "number", "minimum": 0.5, "exclusiveMinimum": True}},
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer", "minimum": -10}}},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "minimum": -10}},
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer", "maximum": 999}}},
            [(("score",), 100.0, BoundDirection.MAX, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "maximum": 999}},
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}},
            [(("score",), 100.0, BoundDirection.MAX, False)],
            {"type": "object", "properties": {"score": {"type": "integer", "maximum": 100}}},
        ),
        (
            {"type": "object", "properties": {"name": {"type": "string"}}},
            [(("name",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"name": {"type": "string"}}},
        ),
        (
            {"type": "object", "properties": {"score": {"type": ["integer", "null"]}}},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {
                "type": "object",
                "properties": {"score": {"type": ["integer", "null"], "minimum": 0}},
            },
        ),
        (
            {"type": "object", "properties": {}},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {}},
        ),
        (True, [(("score",), 0.0, BoundDirection.MIN, False)], True),
        ({"type": "string"}, [(("score",), 0.0, BoundDirection.MIN, False)], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"score": {"type": "integer"}}},
                    {"type": "string"},
                ]
            },
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"score": {"type": "integer", "minimum": 0}},
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            [((), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        ),
    ],
    ids=[
        "integer-min-inclusive",
        "integer-max-exclusive-draft4",
        "number-decimal-bound",
        "existing-min-not-overwritten",
        "existing-max-not-overwritten",
        "max-inclusive-draft4",
        "non-numeric-property-skipped",
        "type-union-with-null-applies",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "empty-path-observation-skipped",
    ],
)
def test_numeric_bound_adjustment_applies_correctly_draft4(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(
            NumericBoundAdjustment(), input_schema, _build_numeric_bound_observations(*items), case_factory
        )
        == expected
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0}}},
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}},
            [(("score",), 0.0, BoundDirection.MIN, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "exclusiveMinimum": 0}},
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}},
            [(("score",), 100.0, BoundDirection.MAX, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "exclusiveMaximum": 100}},
            },
        ),
        (
            {"type": "object", "properties": {"price": {"type": "number"}}},
            [(("price",), 0.5, BoundDirection.MIN, True)],
            {
                "type": "object",
                "properties": {"price": {"type": "number", "exclusiveMinimum": 0.5}},
            },
        ),
    ],
    ids=[
        "integer-min-inclusive",
        "integer-min-exclusive-draft2020",
        "integer-max-exclusive-draft2020",
        "number-decimal-exclusive-draft2020",
    ],
)
def test_numeric_bound_adjustment_applies_correctly_draft2020(input_schema, items, expected, openapi_31_case_factory):
    out = NumericBoundAdjustment().apply(
        operation=openapi_31_case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_numeric_bound_observations(*items),
    )
    _assert_valid_schema_object(input_schema, out, draft="3.1")
    assert out == expected


def test_numeric_bound_adjustment_tightens_a_format_derived_bound(case_factory):
    # `format: int32` is widened into real keywords before generation; a server-reported bound is
    # sharper information than the width of the integer type and must win over it.
    schema = {
        "type": "object",
        "properties": {"month": {"type": "integer", "format": "int32", "minimum": -(2**31), "maximum": 2**31 - 1}},
    }
    out = NumericBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.QUERY,
        schema=schema,
        observations=_build_numeric_bound_observations(
            (("month",), 1.0, BoundDirection.MIN, False),
            (("month",), 12.0, BoundDirection.MAX, False),
        ),
    )
    assert out["properties"]["month"]["minimum"] == 1
    assert out["properties"]["month"]["maximum"] == 12


def test_numeric_bound_adjustment_reaches_array_items(case_factory):
    # A repeated query parameter reports its bound under the field name while the constraint
    # belongs to each element.
    schema = {"type": "object", "properties": {"month": {"type": "array", "items": {"type": "integer"}}}}
    out = NumericBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.QUERY,
        schema=schema,
        observations=_build_numeric_bound_observations(
            (("month",), 1.0, BoundDirection.MIN, False),
            (("month",), 12.0, BoundDirection.MAX, False),
        ),
    )
    assert out == {
        "type": "object",
        "properties": {"month": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 12}}},
    }


def _build_pattern_observations(*items: tuple[tuple[str | int, ...], str]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.PATTERN,
            raw_message=f'must match "{regex}"',
            payload=PatternPayload(regex=regex),
        )
        for path, regex in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"code": {"type": "string"}}},
            [(("code",), "[A-Z]{2,4}")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "[A-Z]{2,4}"}},
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string", "pattern": "[a-z]+"}}},
            [(("code",), "[A-Z]+")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "[a-z]+"}},
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}},
            [(("age",), "[0-9]+")],
            {"type": "object", "properties": {"age": {"type": "integer"}}},
        ),
        (
            {"type": "object", "properties": {"code": {"type": ["string", "null"]}}},
            [(("code",), "[A-Z]+")],
            {
                "type": "object",
                "properties": {"code": {"type": ["string", "null"], "pattern": "[A-Z]+"}},
            },
        ),
        (
            {"type": "object", "properties": {}},
            [(("code",), "[A-Z]+")],
            {"type": "object", "properties": {}},
        ),
        (True, [(("code",), "[A-Z]+")], True),
        ({"type": "string"}, [(("code",), "[A-Z]+")], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"code": {"type": "string"}}},
                    {"type": "string"},
                ]
            },
            [(("code",), "[A-Z]+")],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"code": {"type": "string", "pattern": "[A-Z]+"}},
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"phone": {"type": "string"}},
                    }
                },
            },
            [(("contact", "phone"), "\\+?\\d{3,15}")],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"phone": {"type": "string", "pattern": "\\+?\\d{3,15}"}},
                    }
                },
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}},
            [(("code",), "\\p{L}+")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": normalize_regex("\\p{L}+")}},
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}},
            [(("code",), "\\A[A-Z]+\\Z")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "^[A-Z]+$"}},
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}},
            [(("code",), "[A-Z(unbalanced")],
            {"type": "object", "properties": {"code": {"type": "string"}}},
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}},
            [((), "[A-Z]+")],
            {"type": "object", "properties": {"code": {"type": "string"}}},
        ),
    ],
    ids=[
        "string-property-pattern-injected",
        "existing-pattern-preserved",
        "non-string-property-skipped",
        "type-union-with-null-pattern-injected",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-pattern-injected",
        "pcre-unicode-property-translated",
        "python-anchors-translated-to-ecma",
        "invalid-untranslatable-pattern-skipped",
        "empty-path-observation-skipped",
    ],
)
def test_pattern_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(PatternAdjustment(), input_schema, _build_pattern_observations(*items), case_factory)
        == expected
    )


def _build_type_mismatch_observations(
    *items: tuple[tuple[str | int, ...], str],
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message=f'Cannot deserialize value of type `{type_name}` from String "..."',
            payload=TypeMismatchPayload(type_name=type_name),
        )
        for path, type_name in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"hire_date": {"type": "string"}}},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": "string", "format": "date"}},
            },
        ),
        (
            {"type": "object", "properties": {"started_at": {"type": "string"}}},
            [(("started_at",), "java.time.LocalDateTime")],
            {
                "type": "object",
                "properties": {"started_at": {"type": "string", "format": "date-time"}},
            },
        ),
        (
            {"type": "object", "properties": {"created_at": {"type": "string"}}},
            [(("created_at",), "java.time.Instant")],
            {
                "type": "object",
                "properties": {"created_at": {"type": "string", "format": "date-time"}},
            },
        ),
        (
            {"type": "object", "properties": {"token": {"type": "string"}}},
            [(("token",), "java.util.UUID")],
            {
                "type": "object",
                "properties": {"token": {"type": "string", "format": "uuid"}},
            },
        ),
        (
            {"type": "object", "properties": {"website": {"type": "string"}}},
            [(("website",), "java.net.URL")],
            {
                "type": "object",
                "properties": {"website": {"type": "string", "format": "uri"}},
            },
        ),
        (
            {"type": "object", "properties": {"hire_date": {"type": "string", "format": "date"}}},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": "string", "format": "date"}},
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}},
            [(("x",), "com.example.Custom")],
            {"type": "object", "properties": {"x": {"type": "string"}}},
        ),
        (
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            [(("x",), "java.time.LocalDate")],
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        ),
        (
            {"type": "object", "properties": {"hire_date": {"type": ["string", "null"]}}},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": ["string", "null"], "format": "date"}},
            },
        ),
        (
            {"type": "object", "properties": {}},
            [(("hire_date",), "java.time.LocalDate")],
            {"type": "object", "properties": {}},
        ),
        (True, [(("hire_date",), "java.time.LocalDate")], True),
        ({"type": "string"}, [(("hire_date",), "java.time.LocalDate")], {"type": "string"}),
        (
            {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "object",
                        "properties": {"hire_date": {"type": "string"}},
                    }
                },
            },
            [(("owner", "hire_date"), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "object",
                        "properties": {"hire_date": {"type": "string", "format": "date"}},
                    }
                },
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "addresses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"created_on": {"type": "string"}},
                        },
                    }
                },
            },
            [(("addresses", 0, "created_on"), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {
                    "addresses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"created_on": {"type": "string", "format": "date"}},
                        },
                    }
                },
            },
        ),
        (
            {
                "type": "object",
                "properties": {"addresses": {"type": "array"}},
            },
            [(("addresses", 0, "created_on"), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"addresses": {"type": "array"}},
            },
        ),
    ],
    ids=[
        "localdate-to-date",
        "localdatetime-to-date-time",
        "instant-to-date-time",
        "uuid-to-uuid",
        "url-to-uri",
        "existing-format-preserved",
        "unmapped-type-passthrough",
        "non-string-property-skipped",
        "type-union-with-null-applies",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "nested-path-applies-format",
        "array-element-applies-format-via-items",
        "array-without-items-passes-through",
    ],
)
def test_type_mismatch_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(
            TypeMismatchAdjustment(), input_schema, _build_type_mismatch_observations(*items), case_factory
        )
        == expected
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"age": {"type": "string"}}},
            [(("age",), "integer")],
            {"type": "object", "properties": {"age": {"type": "integer"}}},
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}},
            [(("age",), "integer")],
            {"type": "object", "properties": {"age": {"type": "integer"}}},
        ),
        (
            {
                "type": "object",
                "properties": {"age": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
            },
            [(("age",), "integer")],
            {
                "type": "object",
                "properties": {"age": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
            },
        ),
        (
            {
                "type": "object",
                "properties": {"age": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
            },
            [(("age",), "integer")],
            {
                "type": "object",
                "properties": {"age": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
            },
        ),
        (
            {
                "type": "object",
                "properties": {"age": {"type": ["string", "integer"]}},
            },
            [(("age",), "integer")],
            {
                "type": "object",
                "properties": {"age": {"type": ["string", "integer"]}},
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}},
            [(("x",), "boolean")],
            {"type": "object", "properties": {"x": {"type": "boolean"}}},
        ),
        (
            {"type": "object", "properties": {"hire_date": {"type": "string"}}},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": "string", "format": "date"}},
            },
        ),
        (
            # Schema declares a sub-object; rewriting `type` to a scalar would
            # leave `properties` orphaned. Conservative: skip.
            {
                "type": "object",
                "properties": {"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
            },
            [(("profile",), "integer")],
            {
                "type": "object",
                "properties": {"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
            },
        ),
        (
            # Schema declares an array; rewriting `type` to a scalar would
            # leave `items` orphaned. Conservative: skip.
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            },
            [(("tags",), "integer")],
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            },
        ),
    ],
    ids=[
        "drf-token-rewrites-scalar-type",
        "drf-token-noop-when-matching",
        "composed-anyOf-skip",
        "composed-oneOf-skip",
        "type-list-skip",
        "drf-token-boolean",
        "java-fqn-regression",
        "drf-token-skipped-when-existing-type-is-object",
        "drf-token-skipped-when-existing-type-is-array",
    ],
)
def test_type_mismatch_adjustment_handles_drf_and_java_payloads(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(
            TypeMismatchAdjustment(), input_schema, _build_type_mismatch_observations(*items), case_factory
        )
        == expected
    )


def _build_enum_observations(*items: tuple[tuple[str | int, ...], tuple[str, ...]]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.ENUM,
            raw_message=f"not one of the values accepted for Enum class: [{', '.join(values)}]",
            payload=EnumPayload(values=values),
        )
        for path, values in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"role": {"type": "string"}}},
            [(("role",), ("USER", "ADMIN"))],
            {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["USER", "ADMIN"]}},
            },
        ),
        (
            {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["GUEST"]}},
            },
            [(("role",), ("USER", "ADMIN"))],
            {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["GUEST"]}},
            },
        ),
        (
            {"type": "object", "properties": {"role": {"type": "integer"}}},
            [(("role",), ("USER", "ADMIN"))],
            {"type": "object", "properties": {"role": {"type": "integer"}}},
        ),
        (
            {"type": "object", "properties": {"role": {"type": ["string", "null"]}}},
            [(("role",), ("USER", "ADMIN"))],
            {
                "type": "object",
                "properties": {"role": {"type": ["string", "null"], "enum": ["USER", "ADMIN"]}},
            },
        ),
        (
            {"type": "object", "properties": {}},
            [(("role",), ("USER", "ADMIN"))],
            {"type": "object", "properties": {}},
        ),
        (True, [(("role",), ("USER", "ADMIN"))], True),
        ({"type": "string"}, [(("role",), ("USER", "ADMIN"))], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"role": {"type": "string"}}},
                    {"type": "string"},
                ]
            },
            [(("role",), ("USER", "ADMIN"))],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"role": {"type": "string", "enum": ["USER", "ADMIN"]}},
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "account": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    }
                },
            },
            [(("account", "status"), ("ACTIVE", "ARCHIVED"))],
            {
                "type": "object",
                "properties": {
                    "account": {
                        "type": "object",
                        "properties": {"status": {"type": "string", "enum": ["ACTIVE", "ARCHIVED"]}},
                    }
                },
            },
        ),
        (
            {"type": "object", "properties": {"role": {"type": "string"}}},
            [((), ("USER",))],
            {"type": "object", "properties": {"role": {"type": "string"}}},
        ),
    ],
    ids=[
        "string-property-enum-injected",
        "existing-enum-preserved",
        "non-string-property-skipped",
        "type-union-with-null-enum-injected",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-enum-injected",
        "empty-path-observation-skipped",
    ],
)
def test_enum_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    assert (
        _apply_body_adjustment(EnumAdjustment(), input_schema, _build_enum_observations(*items), case_factory)
        == expected
    )


def test_size_bound_adjustment_preserves_stricter_existing_bound(case_factory):
    # Schema's `maxLength: 10` is tighter than server's `max: 15` — keep the schema's.
    schema = {"type": "object", "properties": {"username": {"type": "string", "maxLength": 10}}}
    assert _apply_body_adjustment(
        SizeBoundAdjustment(), schema, _build_size_bound_observations((("username",), 0, 15)), case_factory
    ) == {
        "type": "object",
        "properties": {"username": {"type": "string", "minLength": 0, "maxLength": 10}},
    }


def test_size_bound_adjustment_min_only_payload(case_factory):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    obs = (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="too short",
            payload=SizeBoundPayload(min=3, max=None),
        ),
    )
    assert _apply_body_adjustment(SizeBoundAdjustment(), schema, obs, case_factory) == {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 3}},
    }


def test_size_bound_adjustment_max_only_payload(case_factory):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    obs = (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="too long",
            payload=SizeBoundPayload(min=None, max=20),
        ),
    )
    assert _apply_body_adjustment(SizeBoundAdjustment(), schema, obs, case_factory) == {
        "type": "object",
        "properties": {"name": {"type": "string", "maxLength": 20}},
    }


def test_apply_adjustments_does_not_mutate_caller_schema(case_factory):
    # Callers cache the input schema; the dispatcher must clone before mutating
    # so adjustment-internal mutation never leaks back to the caller.
    original = {
        "type": "object",
        "properties": {"username": {"type": "string"}},
    }
    snapshot = json.loads(json.dumps(original))

    case = case_factory()
    store = ErrorFeedbackStore()
    blank_observation = Observation(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
        parameter_path=("username",),
        kind=ObservationKind.MUST_NOT_BE_BLANK,
        raw_message="must not be blank",
    )
    size_observation = Observation(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
        parameter_path=("username",),
        kind=ObservationKind.SIZE_BOUND,
        raw_message="size must be between 3 and 8",
        payload=SizeBoundPayload(min=3, max=8),
    )
    for o in (blank_observation, blank_observation, size_observation, size_observation):
        store.record(o)

    out = apply_adjustments(
        operation=case.operation,
        location=ParameterLocation.BODY,
        schema=original,
        store=store,
    )
    _assert_valid_schema_object(original, out)
    assert original == snapshot
    assert out is not original
    assert out["properties"]["username"] == {"type": "string", "minLength": 3, "maxLength": 8}
    assert out["required"] == ["username"]


def test_apply_adjustments_bounds_an_untyped_property(case_factory):
    case = case_factory()
    store = ErrorFeedbackStore()
    for kind, payload in (
        (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
        (ObservationKind.NUMERIC_BOUND, NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False)),
        (ObservationKind.NUMERIC_BOUND, NumericBoundPayload(bound=5.0, direction=BoundDirection.MAX, exclusive=False)),
    ):
        store.record(
            Observation(
                operation_label=case.operation.label,
                location=ParameterLocation.BODY,
                parameter_path=("priority",),
                kind=kind,
                raw_message="Invalid value specified for `priority`",
                payload=payload,
            )
        )
    schema = {"type": "object", "properties": {"priority": {}}}
    out = apply_adjustments(operation=case.operation, location=ParameterLocation.BODY, schema=schema, store=store)
    _assert_valid_schema_object(schema, out)
    assert out == {"type": "object", "properties": {"priority": {"type": "integer", "minimum": 1, "maximum": 5}}}


@pytest.mark.parametrize(
    "input_schema, path, expected",
    [
        pytest.param(
            {"type": "object", "properties": {"a": {}}},
            (),
            {"type": "object", "properties": {"a": {}}, "additionalProperties": False},
            id="top-level-object",
        ),
        pytest.param(
            {"oneOf": [{"type": "object", "properties": {"a": {}}}, {"type": "object", "properties": {"b": {}}}]},
            (),
            {
                "oneOf": [
                    {"type": "object", "properties": {"a": {}}, "additionalProperties": False},
                    {"type": "object", "properties": {"b": {}}, "additionalProperties": False},
                ]
            },
            id="oneof-branches",
        ),
        pytest.param(
            {"type": "object", "properties": {"address": {"type": "object", "properties": {"city": {}}}}},
            ("address",),
            {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "properties": {"city": {}}, "additionalProperties": False}
                },
            },
            id="nested-object",
        ),
        pytest.param(
            {"type": "integer"},
            (),
            {"type": "integer"},
            id="non-object-noop",
        ),
    ],
)
def test_additional_properties_adjustment_forbids_extras(input_schema, path, expected, case_factory):
    assert (
        _apply_body_adjustment(
            AdditionalPropertiesAdjustment(),
            input_schema,
            (
                Observation(
                    operation_label="POST /api/users",
                    location=ParameterLocation.BODY,
                    parameter_path=path,
                    kind=ObservationKind.FORBIDS_ADDITIONAL_PROPERTIES,
                    raw_message="Extra inputs are not permitted",
                ),
            ),
            case_factory,
        )
        == expected
    )


@pytest.mark.parametrize(
    "input_schema, path, expected",
    [
        pytest.param(True, (), True, id="boolean-schema"),
        pytest.param(
            {"type": "object", "properties": {"a": {}}},
            ("missing",),
            {"type": "object", "properties": {"a": {}}},
            id="unknown-nested-property",
        ),
    ],
)
def test_additional_properties_adjustment_skips_unreachable_targets(input_schema, path, expected, case_factory):
    out = AdditionalPropertiesAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=(
            Observation(
                operation_label="POST /api/users",
                location=ParameterLocation.BODY,
                parameter_path=path,
                kind=ObservationKind.FORBIDS_ADDITIONAL_PROPERTIES,
                raw_message="Extra inputs are not permitted",
            ),
        ),
    )
    assert out == expected


def test_pipeline_forwards_case_to_parser(case_factory, response_factory):
    received: list[Case] = []

    class _RecordingSpringParser(SpringParser):
        def parse(self, *, operation, body, case):
            received.append(case)
            return super().parse(operation=operation, body=body, case=case)

    pipeline = FeedbackPipeline([_RecordingSpringParser()])
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=SPRING_MESSAGES,
            content_type="application/json",
            status_code=400,
        )
    )
    pipeline.parse(operation=case.operation, case=case, response=response)
    assert received == [case]


def _record(store, *, operation, case, body, response_factory):
    _reset_pipeline_for_tests()
    response = Response.from_any(
        response_factory.requests(
            content=json.dumps(body).encode(),
            content_type="application/json",
            status_code=400,
        )
    )
    record_response(store=store, operation=operation, case=case, response=response)


@pytest.mark.parametrize(
    ("operation_path", "case_kwargs", "rejected_value", "type_name", "expected_location", "expected_path"),
    [
        # Two body fields share the rejected value -> ambiguous, no observation.
        (
            "/api/items",
            {"body": {"identifier": "DELTA-2026-XYZ", "fallback_id": "DELTA-2026-XYZ", "count": 99}, "method": "POST"},
            "DELTA-2026-XYZ",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Rejected value lives in a query parameter, not the body.
        (
            "/api/reports",
            {"query": {"from": "dd-MM-yyyy-HHmm", "limit": "10"}, "method": "GET"},
            "dd-MM-yyyy-HHmm",
            "java.time.LocalDateTime",
            ParameterLocation.QUERY,
            ("from",),
        ),
        # Rejected value isn't in the request — likely a server-side default.
        (
            "/api/users",
            {"body": {"name": "alice", "score": 42}, "method": "POST"},
            "2024-01-15",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Nested ambiguity: same value in both `shipping.trackingNumber` and `items[0].sku`.
        (
            "/api/orders",
            {
                "body": {"shipping": {"trackingNumber": "TRK-2026-AABB"}, "items": [{"sku": "TRK-2026-AABB"}]},
                "method": "POST",
            },
            "TRK-2026-AABB",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Distinct nested match — only one candidate.
        (
            "/api/orders",
            {
                "body": {"shipping": {"trackingNumber": "TRK-2026-AABB"}, "items": [{"sku": "DIFFERENT-VALUE"}]},
                "method": "POST",
            },
            "TRK-2026-AABB",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            ("shipping", "trackingNumber"),
        ),
    ],
    ids=[
        "ambiguous-flat-body",
        "query-attribution",
        "value-not-in-request",
        "ambiguous-nested",
        "distinct-nested",
    ],
)
def test_field_inference_attribution_through_pipeline(
    make_operation,
    case_factory,
    response_factory,
    operation_path,
    case_kwargs,
    rejected_value,
    type_name,
    expected_location,
    expected_path,
):
    method = case_kwargs["method"]
    operation = make_operation(method=method.lower(), path=operation_path)
    case = case_factory(operation=operation, **case_kwargs)
    response_body = {"message": f'Cannot deserialize value of type `{type_name}` from String "{rejected_value}"'}

    store = ErrorFeedbackStore()
    for _ in range(2):
        _record(store, operation=operation, case=case, body=response_body, response_factory=response_factory)

    expected: tuple[Observation, ...] = (
        ()
        if expected_path is None
        else (
            Observation(
                operation_label=operation.label,
                location=expected_location,
                parameter_path=expected_path,
                kind=ObservationKind.TYPE_MISMATCH,
                raw_message=response_body["message"],
                payload=TypeMismatchPayload(type_name=type_name),
            ),
        )
    )
    assert store.observations(operation_label=operation.label, location=expected_location) == expected


# Realistic Jackson 400 envelopes for typical Spring deployments without `INCLUDE_FIELD_PATH_IN_ERRORS`.
_JACKSON_NO_REFERENCE_CHAIN_CASES: tuple[dict, ...] = (
    {
        "id": "localdate-dash-format",
        "method": "POST",
        "path": "/api/records",
        "body": {"employeeId": 7, "projectId": 12, "commitDate": "dd-MM-yyyy", "comment": "x", "billable": True},
        "response": {
            "message": 'JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String "dd-MM-yyyy"'
        },
        "expected_path": ("commitDate",),
        "expected_payload": TypeMismatchPayload(type_name="java.time.LocalDate"),
    },
    {
        "id": "localdate-slash-format",
        "method": "POST",
        "path": "/api/profiles",
        "body": {"name": "Alice", "hireDate": "MM/dd/yyyy", "departmentId": 3},
        "response": {"message": 'Cannot deserialize value of type `java.time.LocalDate` from String "MM/dd/yyyy"'},
        "expected_path": ("hireDate",),
        "expected_payload": TypeMismatchPayload(type_name="java.time.LocalDate"),
    },
)


@pytest.mark.parametrize("payload", _JACKSON_NO_REFERENCE_CHAIN_CASES, ids=lambda p: p["id"])
def test_field_inference_jackson_envelopes_without_reference_chain(
    payload, make_operation, case_factory, response_factory
):
    operation = make_operation(method=payload["method"].lower(), path=payload["path"])
    case = case_factory(operation=operation, body=payload["body"], method=payload["method"])

    store = ErrorFeedbackStore()
    for _ in range(2):
        _record(store, operation=operation, case=case, body=payload["response"], response_factory=response_factory)

    assert store.observations(operation_label=operation.label, location=ParameterLocation.BODY) == (
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.BODY,
            parameter_path=payload["expected_path"],
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message=payload["response"]["message"],
            payload=payload["expected_payload"],
        ),
    )


def _build_unexpected_property_observations(*paths: tuple[str | int, ...]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=p,
            kind=ObservationKind.UNEXPECTED_PROPERTY,
            raw_message="unrecognized field",
        )
        for p in paths
    )


@pytest.mark.parametrize(
    "input_schema, paths, expected",
    [
        pytest.param(
            {"type": "object", "properties": {"shadow": {}, "ok": {}}, "required": ["shadow"]},
            [("shadow",)],
            {"type": "object", "properties": {"ok": {}}},
            id="properties-and-required-both-cleared",
        ),
        pytest.param(
            {"type": "object", "properties": {"shadow": {}}},
            [("shadow",)],
            {"type": "object", "properties": {}},
            id="properties-only-cleared-when-not-required",
        ),
        pytest.param(
            {"type": "object", "properties": {"ok": {}}, "required": ["shadow"]},
            [("shadow",)],
            {"type": "object", "properties": {"ok": {}}},
            id="required-only-cleared-when-absent-from-properties",
        ),
        pytest.param(
            {"type": "object", "properties": {"a": {}}, "required": ["b"]},
            [("shadow",)],
            {"type": "object", "properties": {"a": {}}, "required": ["b"]},
            id="absent-from-both-noop",
        ),
        pytest.param(
            {"required": ["shadow"]},
            [("shadow",)],
            {},
            id="properties-key-absent",
        ),
        pytest.param(
            {"properties": {"shadow": {}}},
            [("shadow",)],
            {"properties": {}},
            id="required-key-absent",
        ),
        pytest.param(
            True,
            [("shadow",)],
            True,
            id="bool-schema-passthrough",
        ),
        pytest.param(
            {"type": "integer"},
            [("shadow",)],
            {"type": "integer"},
            id="non-object-root-no-targets-passthrough",
        ),
        pytest.param(
            {"type": "object", "properties": {"shadow": {"properties": {"deep": {}}}}, "required": ["shadow"]},
            [("shadow", "deep")],
            {"type": "object", "properties": {}},
            id="only-first-segment-applied",
        ),
        pytest.param(
            {
                "oneOf": [
                    {"type": "object", "properties": {"shadow": {}}, "required": ["shadow"]},
                    {"type": "object", "properties": {"keep": {}}},
                ]
            },
            [("shadow",)],
            {
                "oneOf": [
                    {"type": "object", "properties": {}},
                    {"type": "object", "properties": {"keep": {}}},
                ]
            },
            id="oneof-branches-cascaded",
        ),
        pytest.param(
            {
                "anyOf": [
                    {"type": "object", "properties": {"shadow": {}}},
                    {"type": "object", "properties": {"shadow": {}}, "required": ["shadow"]},
                ]
            },
            [("shadow",)],
            {
                "anyOf": [
                    {"type": "object", "properties": {}},
                    {"type": "object", "properties": {}},
                ]
            },
            id="anyof-branches-cascaded",
        ),
        pytest.param(
            {
                "allOf": [
                    {"type": "object", "properties": {"shadow": {}}, "required": ["shadow"]},
                    {"type": "object", "properties": {"other": {}}},
                ]
            },
            [("shadow",)],
            {
                "allOf": [
                    {"type": "object", "properties": {}},
                    {"type": "object", "properties": {"other": {}}},
                ]
            },
            id="allof-branches-cascaded",
        ),
        pytest.param(
            {"type": "object", "properties": {"a": {}, "b": {}, "ok": {}}, "required": ["a", "b"]},
            [("a",), ("b",)],
            {"type": "object", "properties": {"ok": {}}},
            id="multiple-observations",
        ),
    ],
)
def test_unexpected_property_adjustment_applies_correctly(input_schema, paths, expected, case_factory):
    assert (
        _apply_body_adjustment(
            UnexpectedPropertyAdjustment(), input_schema, _build_unexpected_property_observations(*paths), case_factory
        )
        == expected
    )


def test_unexpected_property_adjustment_drops_empty_required(case_factory):
    # Empty `required` violates the OpenAPI meta-schema and crashes Hypothesis draws.
    input_schema = {"type": "object", "properties": {"shadow": {}}, "required": ["shadow"]}
    out = _apply_body_adjustment(
        UnexpectedPropertyAdjustment(), input_schema, _build_unexpected_property_observations(("shadow",)), case_factory
    )
    assert "required" not in out


def _record_body_observation(store, operation, *, kind, parameter_path):
    for _ in range(MAX_ENTRIES_PER_BUCKET):
        store.record(
            Observation(
                operation_label=operation.label,
                location=ParameterLocation.BODY,
                parameter_path=parameter_path,
                kind=kind,
                raw_message="must not be blank",
            )
        )


@pytest.mark.parametrize(
    "kind, parameter_path, expected",
    [
        pytest.param(ObservationKind.MUST_NOT_BE_BLANK, (), True, id="empty-path-must-not-be-blank"),
        pytest.param(ObservationKind.MUST_NOT_BE_BLANK, ("email",), True, id="field-path-must-not-be-blank"),
        pytest.param(ObservationKind.MUST_NOT_BE_BLANK, ("user", "email"), True, id="nested-path-must-not-be-blank"),
        pytest.param(ObservationKind.UNEXPECTED_PROPERTY, (), False, id="empty-path-unexpected-property"),
    ],
)
def test_body_required_per_feedback_with_store(make_operation, kind, parameter_path, expected):
    operation = make_operation()
    store = ErrorFeedbackStore()
    _record_body_observation(store, operation, kind=kind, parameter_path=parameter_path)
    assert _body_required_per_feedback(operation, store) is expected


def test_body_required_per_feedback_returns_false_with_no_store(make_operation):
    assert _body_required_per_feedback(make_operation(), None) is False
