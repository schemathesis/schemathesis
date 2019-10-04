import pytest

from schemathesis.utils import dict_true_values, is_schemathesis_test


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
