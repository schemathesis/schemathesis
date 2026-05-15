from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    ErrorFeedbackStore,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ObservationPayload,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
    observation_fingerprint,
)
from schemathesis.core.parameters import ParameterLocation


def _make(
    *,
    kind: ObservationKind,
    payload: ObservationPayload,
    path: tuple[str, ...] = ("email",),
    location: ParameterLocation = ParameterLocation.BODY,
) -> Observation:
    return Observation(
        operation_label="POST /v1/foo",
        location=location,
        parameter_path=path,
        kind=kind,
        raw_message="",
        payload=payload,
    )


_PAYLOAD_VARIANTS = [
    (ObservationKind.FORMAT, FormatPayload(name="email"), FormatPayload(name="idn-email")),
    (ObservationKind.PATTERN, PatternPayload(regex="^a$"), PatternPayload(regex="^b$")),
    (ObservationKind.ENUM, EnumPayload(values=("a", "b")), EnumPayload(values=("c", "d"))),
    (
        ObservationKind.TYPE_MISMATCH,
        TypeMismatchPayload(type_name="java.lang.String"),
        TypeMismatchPayload(type_name="java.time.LocalDate"),
    ),
]
_PAYLOAD_VARIANT_IDS = ["format", "pattern", "enum", "type_mismatch"]


@pytest.mark.parametrize(("kind", "left", "right"), _PAYLOAD_VARIANTS, ids=_PAYLOAD_VARIANT_IDS)
def test_distinct_payload_variants_keep_separate_slots(kind, left, right):
    store = ErrorFeedbackStore()
    store.record(_make(kind=kind, payload=left))
    store.record(_make(kind=kind, payload=right))
    assert {
        observation.payload
        for observation in store.observations(operation_label="POST /v1/foo", location=ParameterLocation.BODY)
    } == {left, right}


@pytest.mark.parametrize(("kind", "left", "right"), _PAYLOAD_VARIANTS, ids=_PAYLOAD_VARIANT_IDS)
def test_distinct_payload_variants_produce_distinct_fingerprints(kind, left, right):
    assert observation_fingerprint(_make(kind=kind, payload=left)) != observation_fingerprint(
        _make(kind=kind, payload=right)
    )


def test_numeric_bound_min_and_max_remain_distinct():
    store = ErrorFeedbackStore()
    store.record(
        _make(
            kind=ObservationKind.NUMERIC_BOUND,
            payload=NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
        )
    )
    store.record(
        _make(
            kind=ObservationKind.NUMERIC_BOUND,
            payload=NumericBoundPayload(bound=10.0, direction=BoundDirection.MAX, exclusive=False),
        )
    )
    assert {
        observation.payload.direction
        for observation in store.observations(operation_label="POST /v1/foo", location=ParameterLocation.BODY)
    } == {BoundDirection.MIN, BoundDirection.MAX}


def test_size_bound_min_and_max_merge_into_one_canonical():
    # Parsers emit min and max separately; both edges must collapse into one canonical payload.
    store = ErrorFeedbackStore()
    store.record(_make(kind=ObservationKind.SIZE_BOUND, payload=SizeBoundPayload(min=3, max=None)))
    store.record(_make(kind=ObservationKind.SIZE_BOUND, payload=SizeBoundPayload(min=None, max=30)))
    observations = store.observations(operation_label="POST /v1/foo", location=ParameterLocation.BODY)
    assert len(observations) == 1
    assert observations[0].payload == SizeBoundPayload(min=3, max=30)
