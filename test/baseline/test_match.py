from datetime import date

import pytest

from schemathesis.baseline import Baseline, BaselineEntry
from schemathesis.core.failures import ServerError
from schemathesis.openapi.checks import RejectedPositiveData

TODAY = date(2026, 9, 10)
CHECK = "positive_data_acceptance"


def rejected(operation="GET /users", status_code=409):
    return RejectedPositiveData(
        operation=operation,
        message="Valid data should have been accepted",
        status_code=status_code,
        allowed_statuses=["2xx"],
    )


def test_recorded_failure_matches_itself():
    baseline = Baseline(entries=[BaselineEntry.from_failure(rejected(), check=CHECK)])

    assert baseline.match(rejected(), CHECK, today=TODAY).signature == "409"


def test_same_class_with_a_different_signature_does_not_match():
    baseline = Baseline(entries=[BaselineEntry.from_failure(rejected(), check=CHECK)])

    assert baseline.match(rejected(status_code=500), CHECK, today=TODAY) is None


def test_same_signature_on_another_operation_does_not_match():
    baseline = Baseline(entries=[BaselineEntry.from_failure(rejected(), check=CHECK)])

    assert baseline.match(rejected(operation="POST /users"), CHECK, today=TODAY) is None


def test_another_failure_class_does_not_match():
    baseline = Baseline(entries=[BaselineEntry.from_failure(rejected(), check=CHECK)])
    server_error = ServerError(operation="GET /users", status_code=409, message="boom")

    assert baseline.match(server_error, "not_a_server_error", today=TODAY) is None


@pytest.mark.parametrize(
    ("expires", "matches"),
    [("2026-12-01", True), ("2026-09-10", True), ("2026-09-09", False)],
    ids=["future", "expires-today", "past"],
)
def test_expiry_stops_suppression(expires, matches):
    entry = BaselineEntry.from_failure(rejected(), check=CHECK)
    entry.expires = expires
    baseline = Baseline(entries=[entry])

    assert (baseline.match(rejected(), CHECK, today=TODAY) is not None) is matches


def test_the_same_failure_from_another_check_does_not_match():
    baseline = Baseline(entries=[BaselineEntry.from_failure(rejected(), check=CHECK)])

    assert baseline.match(rejected(), "custom_acceptance_check", today=TODAY) is None
