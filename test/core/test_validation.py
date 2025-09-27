import pytest

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.validation import check_header_name


@pytest.mark.parametrize(
    ["parameter", "expected"],
    [
        ("", "Header name should not be empty"),
        ("Invalid\x80Name", "Header name should be ASCII: Invalid\x80Name"),
        ("\nInvalid", "Invalid leading whitespace"),
        ("0\n", "Invalid header name: 0\n"),
    ],
)
def test_check_header_errors(parameter, expected):
    with pytest.raises(InvalidSchema, match=expected):
        check_header_name(parameter)
