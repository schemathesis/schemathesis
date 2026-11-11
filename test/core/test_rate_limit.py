import threading
import time
from email.utils import formatdate

import pytest
from pyrate_limiter import Duration

import schemathesis.graphql
from schemathesis.core.rate_limit import RATE_LIMIT_AUTO_MAX_WAIT, parse_retry_after, parse_units
from test.utils import graphql_url, openapi_url


@pytest.mark.parametrize(
    ("loader", "make_url", "kind"),
    [
        (schemathesis.graphql.from_url, graphql_url, "graphql"),
        (schemathesis.openapi.from_url, openapi_url, "openapi"),
    ],
)
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_maximum_requests(ctx, loader, make_url, kind):
    schema = loader(make_url(ctx))
    schema.config.update(rate_limit="5/h")
    counter = 0
    limit_reached = threading.Event()

    def run():
        nonlocal counter
        for _ in range(5):
            for operation in schema.get_all_operations():
                operation = operation.ok()
                if kind == "graphql":
                    case = operation.Case(body={})
                else:
                    case = operation.Case()
                case.call()
                counter += 1
                if counter == 5:
                    limit_reached.set()

    # The 6th call blocks until the hourly window resets, so the thread is abandoned mid-call
    threading.Thread(target=run, daemon=True).start()
    assert limit_reached.wait(15)
    assert counter == 5


@pytest.mark.parametrize(
    ("rate_str", "expected_limit", "expected_interval"),
    [
        ("1/s", 1, Duration.SECOND),
        ("100/m", 100, Duration.MINUTE),
        ("1000/h", 1000, Duration.HOUR),
    ],
)
def test_parse_units(rate_str, expected_limit, expected_interval):
    limit, interval = parse_units(rate_str)
    assert limit == expected_limit
    assert interval == expected_interval


@pytest.mark.parametrize(
    "header, expected",
    [
        ("30", 30.0),
        ("0", 0.0),
        ("999", float(RATE_LIMIT_AUTO_MAX_WAIT)),
        ("", None),
        ("not-a-date-or-number", None),
        ("-5", 0.0),
    ],
    ids=["delay-seconds", "zero", "exceeds-cap", "empty", "malformed", "negative"],
)
def test_parse_retry_after(header, expected):
    assert parse_retry_after(header) == expected


def test_parse_retry_after_http_date_future():
    result = parse_retry_after(formatdate(timeval=time.time() + 60, usegmt=True))
    assert result is not None
    assert 55 <= result <= 65


def test_parse_retry_after_http_date_past():
    assert parse_retry_after("Thu, 01 Jan 1970 00:00:00 GMT") == 0.0
