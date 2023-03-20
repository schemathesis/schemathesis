import pytest

from schemathesis.specs.openapi.validation import find_numeric_http_status_codes


@pytest.mark.parametrize("value", ([], {"paths": {"/users/": {"post": 42}}}))
def test_find_numeric_http_status_codes_not_found(value):
    assert find_numeric_http_status_codes(value) == []
