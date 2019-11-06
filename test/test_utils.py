import pytest

from schemathesis.utils import are_content_types_equal, dict_true_values, is_schemathesis_test, parse_content_type


def test_is_schemathesis_test(swagger_20):
    # When a test is wrapped with `parametrize`

    @swagger_20.parametrize()
    def test():
        pass

    # Then is should be recognized as a schemathesis test
    assert is_schemathesis_test(test)


@pytest.mark.parametrize("input_dict,expected_dict", [({}, {}), ({"a": 1, "b": 0}, {"a": 1}), ({"abc": None}, {})])
def test_dict_true_values(input_dict, expected_dict):
    assert dict_true_values(**input_dict) == expected_dict


@pytest.mark.parametrize(
    "value, expected",
    (
        ("text/plain", ("text", "plain")),
        ("application/json+problem", ("application", "json+problem")),
        ("application/json;charset=utf-8", ("application", "json")),
        ("application/json/random", ("application", "json/random")),
    ),
)
def test_parse_content_type(value, expected):
    assert parse_content_type(value) == expected


@pytest.mark.parametrize(
    "first, second, expected",
    (
        ("application/json", "application/json", True),
        ("APPLICATION/JSON", "application/json", True),
        ("application/json", "application/json;charset=utf-8", True),
        ("application/json;charset=utf-8", "application/json;charset=utf-8", True),
        ("application/json;charset=utf-8", "application/json", True),
        ("text/plain", "application/json", False),
    ),
)
def test_are_content_types_equal(first, second, expected):
    assert are_content_types_equal(first, second) is expected
