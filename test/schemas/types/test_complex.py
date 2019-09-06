import pytest

from ..utils import as_array, string


@pytest.mark.parametrize(
    "parameter",
    (
        string(name="key1"),
        string(name="key2", maxLength=5),
        string(name="key3", minLength=5),
        string(name="key4", pattern="ab{2}"),
        string(name="key5", minLength=3, maxLength=6, pattern="ab{2}"),
        string(name="key6", format="date"),
        string(name="key7", format="date-time"),
    ),
)
def test_array_of_strings(testdir, parameter):
    testdir.make_test(
        """
validator = {{
    "key1": noop,
    "key2": lambda x: len(x) <= 5,
    "key3": lambda x: len(x) >= 5,
    "key4": lambda x: "abb" in x,
    "key5": lambda x: len(x) in (3, 4, 5, 6) and "abb" in x,
    "key6": assert_date,
    "key7": assert_datetime,
}}["{name}"]
@schema.parametrize()
@settings(max_examples=3)
def test_(case):
    assert_list(case.query["values"])
    for item in case.query["values"]:
        validator(item)
        """.format(
            name=parameter["name"]
        ),
        **as_array(items=parameter, required=True),
    )
    testdir.run_and_assert(passed=1)


def test_array_number_of_items(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=3)
def test_(case):
    assert_list(case.query["values"], lambda x: len(x) == 3)
""",
        **as_array(items={"type": "string"}, minItems=3, maxItems=3, required=True),
    )
    testdir.run_and_assert(passed=1)


def test_array_unique_items(testdir):
    testdir.make_test(
        """
@schema.parametrize()
def test_(case):
    assert_list(case.query["values"], lambda x: len(x) == len(set(x)))
""",
        **as_array(items={"type": "string"}, minItems=3, maxItems=3, uniqueItems=True, required=True),
    )
    testdir.run_and_assert(passed=1)


def test_array_items_list(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=3)
def test_(case):
    values = case.query["values"]
    assert_list(values)
    if len(values) > 0:
        assert_str(values[0])
    if len(values) > 1:
        assert_int(values[1])
""",
        **as_array(items=[{"type": "string"}, {"type": "integer"}], required=True),
    )
    testdir.run_and_assert(passed=1)


def test_array_additional_items_boolean(testdir):
    testdir.make_test(
        """
@schema.parametrize()
def test_(case):
    values = case.query["values"]
    assert_list(values, lambda x: len(x) < 3)
    if len(values) > 0:
        assert_str(values[0])
    if len(values) > 1:
        assert_int(values[1])
""",
        **as_array(items=[{"type": "string"}, {"type": "integer"}], additionalItems=False, required=True),
    )
    testdir.run_and_assert(passed=1)


def test_array_additional_items_schema(testdir):
    testdir.make_test(
        """
@schema.parametrize()
def test_(case):
    values = case.query["values"]
    assert_list(values)
    if len(values) > 0:
        assert_str(values[0])
    if len(values) > 1:
        assert_int(values[1])
    if len(values) > 2:
        assert_str(values[2])
""",
        **as_array(items=[{"type": "string"}, {"type": "integer"}], additionalItems={"type": "string"}, required=True),
    )
    testdir.run_and_assert(passed=1)
