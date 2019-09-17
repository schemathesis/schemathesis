import pytest

from ..utils import as_param, integer, string


@pytest.mark.parametrize(
    "parameter",
    (
        integer(name="id1", required=True),
        integer(name="id2", maximum=5, required=True),
        integer(name="id3", maximum=5, exclusiveMaximum=True, required=True),
        integer(name="id4", maximum=5, multipleOf=4, required=True),
        integer(name="id5", minimum=5, required=True),
        integer(name="id6", minimum=5, exclusiveMinimum=True, required=True),
        integer(name="id7", minimum=5, multipleOf=4, required=True),
    ),
)
def test_integer(testdir, parameter):
    testdir.make_test(
        """
validator = {{
    "id1": noop,
    "id2": lambda x: x <= 5,
    "id3": lambda x: x < 5,
    "id4": lambda x: x % 4 == 0,
    "id5": lambda x: x >= 5,
    "id6": lambda x: x > 5,
    "id7": lambda x: x % 4 == 0,
}}["{name}"]
@schema.parametrize()
@settings(max_examples=3)
def test_(case):
    assert case.path == "/v1/users"
    assert case.method in ("GET", "POST")
    validator(case.query["{name}"])
        """.format(
            name=parameter["name"]
        ),
        **as_param(parameter),
    )
    testdir.run_and_assert(passed=1)


@pytest.mark.parametrize(
    "parameter",
    (
        string(name="key1", required=True),
        string(name="key2", maxLength=5, required=True),
        string(name="key3", minLength=5, required=True),
        string(name="key4", pattern="ab{2}", required=True),
        string(name="key5", minLength=3, maxLength=6, pattern="ab{2}", required=True),
        string(name="key6", format="date", required=True),
        string(name="key7", format="date-time", required=True),
    ),
)
def test_string(testdir, parameter):
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
    assert case.path == "/v1/users"
    assert case.method in ("GET", "POST")
    validator(case.query["{name}"])
        """.format(
            name=parameter["name"]
        ),
        **as_param(parameter),
    )
    testdir.run_and_assert(passed=1)
