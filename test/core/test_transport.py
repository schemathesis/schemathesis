import pytest

from schemathesis.core.transport import expand_status_code


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (500, [500]),
        ("500", [500]),
        ("50X", list(range(500, 510))),
        ("50x", list(range(500, 510))),
    ],
)
def test_expand_status_code(value, expected):
    assert expand_status_code(value) == expected
