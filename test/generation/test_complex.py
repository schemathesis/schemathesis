import pytest

from ..utils import as_array, string


@pytest.mark.parametrize(
    "name, parameter",
    (
        ("key1", string()),
        ("key2", string(maxLength=5)),
        ("key3", string(minLength=5)),
        ("key4", string(pattern="ab{2}")),
        ("key5", string(minLength=3, maxLength=6, pattern="ab{2}")),
        ("key6", string(format="date")),
        ("key7", string(format="date-time")),
    ),
)
def test_array_of_strings(testdir, name, parameter):
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
@schema.parametrize(method="POST")
@settings(max_examples=3)
def test_(case):
    assert_list(case.body["values"])
    for item in case.body["values"]:
        validator(item)
        """.format(
            name=name
        ),
        **as_array(items=parameter),
    )
    testdir.run_and_assert("-s", passed=1)


def test_array_number_of_items(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=3)
def test_(case):
    assert_list(case.body["values"], lambda x: len(x) == 3)
""",
        **as_array(items={"type": "string"}, minItems=3, maxItems=3),
    )
    testdir.run_and_assert(passed=1)


def test_array_unique_items(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(suppress_health_check=[HealthCheck.filter_too_much])
def test_(case):
    assert_list(case.body["values"], lambda x: len(x) == len(set(x)))
""",
        **as_array(items={"type": "string"}, minItems=3, maxItems=3, uniqueItems=True),
    )
    testdir.run_and_assert(passed=1)


def test_array_items_list(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=3)
def test_(case):
    values = case.body["values"]
    assert_list(values)
    if len(values) > 0:
        assert_str(values[0])
    if len(values) > 1:
        assert_int(values[1])
""",
        **as_array(items=[{"type": "string"}, {"type": "integer"}]),
    )
    testdir.run_and_assert(passed=1)
