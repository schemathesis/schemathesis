from schemathesis.constants import DEFAULT_DEADLINE, RECURSIVE_REFERENCE_ERROR_MESSAGE, USER_AGENT


def test_pytest_parametrize_fixture(testdir):
    # When `pytest_generate_tests` is used on a module level for fixture parametrization
    testdir.make_test(
        """
def pytest_generate_tests(metafunc):
    metafunc.parametrize("inner", ("A", "B"))

@pytest.fixture()
def param(inner):
    return inner * 2

@schema.parametrize()
def test_(request, param, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
    )
    # And there are multiple method/path combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be method/path combos x parameters in `pytest_generate_tests`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize_fixture.py::test_\[GET:/v1/users\]\[P\]\[A\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[GET:/v1/users\]\[P\]\[B\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[POST:/v1/users\]\[P\]\[A\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[POST:/v1/users\]\[P\]\[B\] PASSED",
            r"Hypothesis calls: 4",
        ]
    )


def test_pytest_parametrize_class_fixture(testdir):
    # When `pytest_generate_tests` is used on a class level for fixture parametrization
    testdir.make_test(
        """
class TestAPI:

    def pytest_generate_tests(self, metafunc):
        metafunc.parametrize("inner", ("A", "B"))

    @pytest.fixture()
    def param(self, inner):
        return inner * 2

    @schema.parametrize()
    def test_(self, request, param, case):
        request.config.HYPOTHESIS_CASES += 1
        assert case.full_path == "/v1/users"
        assert case.method in ("GET", "POST")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
    )
    # And there are multiple method/path combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be method/path combos x parameters in `pytest_generate_tests`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[GET:/v1/users\]\[P\]\[A\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[GET:/v1/users\]\[P\]\[B\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[POST:/v1/users\]\[P\]\[A\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[POST:/v1/users\]\[P\]\[B\] PASSED",
            r"Hypothesis calls: 4",
        ]
    )


def test_pytest_collection_regression(testdir):
    # See #429.
    # When in a module scope there is an object that has custom `__getattr__` (a mock for example)
    testdir.make_test(
        """
from unittest.mock import call

def test_schemathesis():
    assert True
""",
    )
    result = testdir.runpytest()
    # It shouldn't be collected as a test
    result.assert_outcomes(passed=1)


def test_pytest_warning(testdir):
    testdir.make_test(
        """
@schema.parametrize()
def test_a(case):
    assert True

@schema.parametrize()
@pytest.mark.parametrize("a", (1, 2))
def test_b(case, a):
    assert True
""",
    )
    # When a test is run with treating warnings as errors
    result = testdir.runpytest("-Werror")
    # There should be no errors. There are no warnings from Schemathesis pytest plugin.
    result.assert_outcomes(passed=3)


def test_default_hypothesis_deadline(testdir):
    testdir.make_test(
        f"""
@schema.parametrize()
def test_a(case):
    assert settings().deadline.microseconds == {DEFAULT_DEADLINE} * 1000

@schema.parametrize()
@settings(max_examples=5)
def test_b(case):
    assert settings().deadline.microseconds == {DEFAULT_DEADLINE} * 1000

@schema.parametrize()
@settings(max_examples=5, deadline=100)
def test_c(case):
    assert settings().deadline.microseconds == 100 * 1000

def test_d():
    assert settings().deadline.microseconds == 200 * 1000
""",
    )
    # When there is a test with Pytest
    result = testdir.runpytest()
    # # Then it should use the global Schemathesis deadline for Hypothesis (DEFAULT_DEADLINE value)
    result.assert_outcomes(passed=4)


def test_schema_given(testdir):
    # When the test uses `schema.given`
    testdir.make_test(
        """
from hypothesis.strategies._internal.core import DataObject

OPERATIONS = []

@schema.parametrize()
@schema.given(data=st.data())
def test(data, case):
    assert isinstance(data, DataObject)
    OPERATIONS.append(f"{case.method} {case.path}")


def test_operations():
    assert OPERATIONS == ['GET /users', 'POST /users']
    """,
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
    )
    # Then its arguments should be proxied to the `hypothesis.given`
    # And be available in the test
    result = testdir.runpytest()
    result.assert_outcomes(passed=3)


def test_invalid_test(testdir):
    # When the test doesn't use the strategy provided in `schema.given`
    testdir.make_test(
        """
@schema.parametrize()
@schema.given(data=st.data())
def test(case):
    pass
    """,
    )
    # Then the test should fail instead of error
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)


def test_failure_reproduction_message(testdir, openapi3_base_url):
    # When a test fails
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"

@schema.parametrize(endpoint="failure")
def test(case):
    response = case.call()
    case.validate_response(response)
    """,
        paths={"/failure": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    # Then there should be a helpful message in the output
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines(
        [
            r".+1. Received a response with 5xx status code: 500",
            r".+2. Received a response with a status code, which is not defined in the schema: 500",
            r".+Declared status codes: 200",
            r".+Run this Python code to reproduce this response:",
            rf".+requests.get\('{openapi3_base_url}/failure', headers={{'User-Agent': '{USER_AGENT}'",
        ]
    )


def test_skip_operations_with_recursive_references(testdir, schema_with_recursive_references):
    # When the test schema contains recursive references
    testdir.make_test(
        """
@schema.parametrize()
def test(case):
    pass""",
        schema=schema_with_recursive_references,
    )
    result = testdir.runpytest("-rs")
    # Then this test should be skipped with a proper error message
    result.assert_outcomes(skipped=1)
    assert RECURSIVE_REFERENCE_ERROR_MESSAGE in result.stdout.str()
