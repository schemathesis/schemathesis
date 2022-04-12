import pytest
from hypothesis import given
from hypothesis import strategies as st

from schemathesis.utils import (
    dict_true_values,
    import_app,
    is_json_media_type,
    is_plain_text_media_type,
    is_schemathesis_test,
    parse_content_type,
)


def test_is_schemathesis_test(swagger_20):
    # When a test is wrapped with `parametrize`

    @swagger_20.parametrize()
    def test():
        pass

    # Then it should be recognized as a schemathesis test
    assert is_schemathesis_test(test)


@pytest.mark.parametrize("input_dict,expected_dict", [({}, {}), ({"a": 1, "b": 0}, {"a": 1}), ({"abc": None}, {})])
def test_dict_true_values(input_dict, expected_dict):
    assert dict_true_values(**input_dict) == expected_dict


@pytest.mark.parametrize(
    "value, expected",
    (
        ("text/plain", ("text", "plain")),
        ("application/problem+json", ("application", "problem+json")),
        ("application/json;charset=utf-8", ("application", "json")),
        ("application/json/random", ("application", "json/random")),
    ),
)
def test_parse_content_type(value, expected):
    assert parse_content_type(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    (
        ("application/problem+json", True),
        ("application/json", True),
        ("application/xml", False),
        ("text/plain", False),
    ),
)
def test_is_json_media_type(value, expected):
    assert is_json_media_type(value) is expected


@pytest.mark.parametrize(
    "value, expected",
    (
        ("text/plain", True),
        ("text/plain;charset=utf-8", True),
        ("application/json", False),
        ("application/problem+json", False),
    ),
)
def test_is_plain_text_media_type(value, expected):
    assert is_plain_text_media_type(value) is expected


@pytest.mark.parametrize(
    "path, exception, match",
    (
        ("", ValueError, "Empty module name"),
        ("foo", ImportError, "No module named 'foo'"),
        ("schemathesis:foo", AttributeError, "module 'schemathesis' has no attribute 'foo'"),
    ),
)
def test_import_app(path, exception, match):
    with pytest.raises(exception, match=match):
        import_app(path)


@given(st.text())
def test_import_app_no_unexpected_exceptions(path):
    # `import_app` should not raise anything else but `ImportError` or `AttributeError` or `ValueError`
    with pytest.raises((ImportError, AttributeError, ValueError)):
        import_app(path)
