import pytest

from scripts.analyze.metrics import Bucket, CallBucket, FailureRef, classify_call


def test_bucket_handler_reached_ratio_zero_when_total_zero():
    assert Bucket().handler_reached_ratio == 0.0


def test_bucket_handler_reached_ratio_division():
    bucket = Bucket(positive_accepted=3, route_rejected=1)
    assert bucket.handler_reached_ratio == 0.75


def test_bucket_useful_ratio_excludes_drift():
    # Drift (P+4xx and N+2xx) reached the handler but did not exercise the operation as intended.
    bucket = Bucket(
        positive_accepted=3, negative_rejected=2, positive_server_error=1, positive_drift=4, negative_drift=2
    )
    assert bucket.useful == 6
    assert bucket.useful_ratio == 0.5


def test_bucket_bump_increments_matching_field():
    bucket = Bucket()
    bucket.bump(CallBucket.POSITIVE_ACCEPTED)
    bucket.bump(CallBucket.POSITIVE_ACCEPTED)
    bucket.bump(CallBucket.NEGATIVE_DRIFT)
    assert (bucket.positive_accepted, bucket.negative_drift, bucket.total) == (2, 1, 3)


def test_failure_ref_fingerprint_excludes_message():
    a = FailureRef(check_name="c", operation_label="GET /x", failure_type="t", message="alpha")
    b = FailureRef(check_name="c", operation_label="GET /x", failure_type="t", message="beta")
    assert a.fingerprint == b.fingerprint
    assert a.message != b.message


def _call(*, status, mode="positive", components=None, matches_route=True):
    return {
        "status": status,
        "overall_mode": mode,
        "components": components or {},
        "matches_route": matches_route,
    }


@pytest.mark.parametrize(
    ("call", "expected_bucket"),
    [
        # Mode + status -> bucket
        (_call(status=200), CallBucket.POSITIVE_ACCEPTED),
        (_call(status=200, mode="negative"), CallBucket.NEGATIVE_DRIFT),
        (_call(status=400, mode="negative"), CallBucket.NEGATIVE_REJECTED),
        # 5xx — split by mode so "crash on valid input" vs "crash on invalid input" remain distinct.
        (_call(status=503, mode="positive"), CallBucket.POSITIVE_SERVER_ERROR),
        (_call(status=502, mode="negative"), CallBucket.NEGATIVE_SERVER_ERROR),
        # Auth
        (_call(status=401), CallBucket.AUTH_REJECTED),
        (_call(status=403), CallBucket.AUTH_REJECTED),
        # Route mismatch -> route_rejected only when method actually differs
        (_call(status=404, matches_route=False), CallBucket.ROUTE_REJECTED),
        (_call(status=405, matches_route=False), CallBucket.ROUTE_REJECTED),
        # Matching route falls through to mode/status logic
        (_call(status=405, matches_route=True, mode="positive"), CallBucket.POSITIVE_DRIFT),
        (_call(status=404, matches_route=True, mode="negative"), CallBucket.NEGATIVE_REJECTED),
        # Other
        (_call(status=302), CallBucket.OTHER),
        (_call(status="transport-error"), CallBucket.OTHER),
    ],
    ids=[
        "p+200",
        "n+200",
        "n+400",
        "p+5xx",
        "n+5xx",
        "401",
        "403",
        "404-method-mismatch",
        "405-method-mismatch",
        "405-matching-route",
        "404-matching-route-negative",
        "3xx",
        "transport-error",
    ],
)
def test_classify_buckets(call, expected_bucket):
    assert classify_call(call).bucket is expected_bucket


def test_classify_positive_drift_surfaces_components_excluding_unknown():
    result = classify_call(
        _call(
            status=422, mode="positive", components={"body": "positive", "headers": "negative", "UNKNOWN": "positive"}
        )
    )
    assert result.bucket is CallBucket.POSITIVE_DRIFT
    assert result.locations_present == ("body", "headers")


@pytest.mark.parametrize(
    "call",
    [
        _call(status=400, mode="negative", components={"body": "negative"}),
        _call(status=503, mode="positive", components={"body": "positive"}),  # positive_server_error
        _call(status=200, mode="negative", components={"body": "negative"}),
    ],
    ids=["negative-rejected", "server-error", "negative-drift"],
)
def test_classify_locations_present_only_for_positive_drift(call):
    assert classify_call(call).locations_present == ()
