from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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
from schemathesis.core.error_feedback.store import MAX_ENTRIES_PER_BUCKET
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


def test_observation_counts_keep_growing_past_the_bucket_cap():
    # The cap bounds what is remembered, not how much an operation has taught.
    store = ErrorFeedbackStore()
    for index in range(MAX_ENTRIES_PER_BUCKET * 2):
        store.record(_make(kind=ObservationKind.MUST_NOT_BE_BLANK, payload=None, path=(f"f{index}",)))

    assert store.observation_counts() == {"POST /v1/foo": MAX_ENTRIES_PER_BUCKET * 2}


def _obs(field: str, *, op: str = "POST /api/users") -> Observation:
    return Observation(
        operation_label=op,
        location=ParameterLocation.BODY,
        parameter_path=(field,),
        kind=ObservationKind.MUST_NOT_BE_BLANK,
        raw_message=f"{field} - must not be blank",
    )


def test_store_dedups_identical_observations_into_one_entry():
    store = ErrorFeedbackStore()
    for _ in range(1553):
        store.record(_obs("email"))
    assert len(store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)) == 1


def test_store_evicts_lowest_count_entry_when_bucket_full():
    store = ErrorFeedbackStore()
    for i in range(MAX_ENTRIES_PER_BUCKET):
        store.record(_obs(f"f{i}"))
        store.record(_obs(f"f{i}"))
    for _ in range(50):
        store.record(_obs("f0"))
    store.record(_obs("new_field"))
    store.record(_obs("new_field"))

    paths = {
        o.parameter_path for o in store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    }
    assert ("f0",) in paths
    assert ("new_field",) in paths
    assert len(paths) == MAX_ENTRIES_PER_BUCKET


def test_store_observations_surface_on_first_record():
    # Parsers only emit observations on a specific framework-string match, so a single
    # occurrence is conclusive — observations propagate to the next phase immediately
    # rather than waiting for a duplicate confirmation.
    store = ErrorFeedbackStore()
    store.record(_obs("email"))
    out = store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    assert len(out) == 1
    assert out[0].parameter_path == ("email",)


def test_store_checkpoint_bumps_generation_and_keeps_observations():
    store = ErrorFeedbackStore()
    store.record(_obs("email"))
    store.record(_obs("email"))
    assert store.generation == 0

    store.checkpoint()
    assert store.generation == 1
    assert len(store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)) == 1

    store.checkpoint()
    assert store.generation == 2


def test_store_record_does_not_bump_generation():
    store = ErrorFeedbackStore()
    store.record(_obs("email"))
    store.record(_obs("email"))
    store.record(_obs("email"))
    assert store.generation == 0


def test_store_keeps_min_and_max_numeric_bounds_for_same_path():
    store = ErrorFeedbackStore()
    min_payload = NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True)
    max_payload = NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=False)
    for payload in (min_payload, min_payload, max_payload, max_payload):
        store.record(
            Observation(
                operation_label="POST /api/users",
                location=ParameterLocation.BODY,
                parameter_path=("qty",),
                kind=ObservationKind.NUMERIC_BOUND,
                raw_message="",
                payload=payload,
            )
        )
    out = store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    assert sorted((o.payload.direction, o.payload.bound) for o in out) == [
        (BoundDirection.MAX, 100.0),
        (BoundDirection.MIN, 0.0),
    ]


def test_store_concurrent_inserts_are_safe():
    store = ErrorFeedbackStore()

    def worker(field_index: int) -> None:
        ob = _obs(f"f{field_index}")
        for _ in range(100):
            store.record(ob)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    out = store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    paths = sorted(o.parameter_path for o in out)
    assert paths == sorted((f"f{i}",) for i in range(8))
