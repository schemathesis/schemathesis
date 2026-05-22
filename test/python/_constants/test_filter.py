import math

import pytest

from schemathesis.python._constants.filter import is_kept


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ACTIVE", True),
        ("InProgress", True),
        ("user@example.com", True),
        ("https://x", True),
        ("hello", True),
        ("multi\nline", False),
        ("my_app.models.User", False),
        ("/etc/passwd", False),
        (".hidden", False),
    ],
)
def test_string_filter(value, expected):
    assert is_kept(value, "string") is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        # 0/1/-1 are unreachable in practice (Hypothesis already drops them),
        # but the filter is defensive.
        (0, False),
        (1, False),
        (-1, False),
        # HTTP statuses.
        (200, False),
        (404, False),
        (500, False),
        # Real candidates.
        (1024, True),
        (12345, True),
        (-200, True),
    ],
)
def test_integer_filter(value, expected):
    assert is_kept(value, "integer") is expected


@pytest.mark.parametrize(
    "value,expected",
    [(0.0, False), (1.0, False), (-1.0, False), (math.inf, False), (math.nan, False), (3.14, True), (0.001, True)],
)
def test_float_filter(value, expected):
    assert is_kept(value, "float") is expected


@pytest.mark.parametrize(
    "value,expected",
    [(b"OK", True), (b"a" * 33, False), (b"hi\nbye", False)],
)
def test_bytes_filter(value, expected):
    assert is_kept(value, "bytes") is expected
