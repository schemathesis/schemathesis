from __future__ import annotations

import time
from email.utils import formatdate

import pytest

from schemathesis.core.rate_limit import RATE_LIMIT_AUTO_MAX_WAIT, parse_retry_after


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
