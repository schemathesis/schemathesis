import pytest

from schemathesis.extra.pytest_plugin import NOT_USED_STATEFUL_TESTING_MESSAGE
from schemathesis.models import MISSING_STATEFUL_ARGUMENT_MESSAGE
from schemathesis.stateful import ParsedData
from schemathesis.utils import NOT_SET

from .apps.utils import OpenAPIVersion


@pytest.mark.parametrize(
    "parameters, body", (({"a": 1}, None), ({"a": 1}, NOT_SET), ({"a": 1}, {"value": 1}), ({"a": 1}, [1, 2, 3]))
)
def test_hashable(parameters, body):
    # All parsed data should be hashable
    hash(ParsedData(parameters, body))


@pytest.fixture
def openapi_version():
    return OpenAPIVersion("3.0")


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_enabled(testdir, app_schema, openapi3_base_url):
    # When "stateful" is used in the "parametrize" decorator
    testdir.make_test(
        f"""
@schema.parametrize(stateful=Stateful.links, method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    case.validate_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be 4 tests in total:
    # 1 - the original one for POST /users
    # 3 - stateful ones:
    #   - POST /users -> GET /users
    #   - POST /users -> GET /users -> PATCH /users
    #   - POST /users -> PATCH /users
    result = testdir.run_and_assert("-v", passed=4)
    result.stdout.re_match_lines(
        [
            r"test_stateful_enabled.py::test_\[POST:/api/users/\] PASSED",
            r"test_stateful_enabled.py::test_\[POST:/api/users/ -> GET:/api/users/{user_id}\] PASSED * \[ 66%\]",
            r"test_stateful_enabled.py::test_"
            r"\[POST:/api/users/ -> GET:/api/users/{user_id} -> PATCH:/api/users/{user_id}\] PASSED * \[ 75%\]",
            r"test_stateful_enabled.py::test_\[POST:/api/users/ -> PATCH:/api/users/{user_id}\] PASSED * \[100%\]",
            r"Hypothesis calls: 8",
        ]
    )


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_enabled_limit(testdir, app_schema, openapi3_base_url):
    # When "stateful" is used in the "parametrize" decorator
    # And "stateful_recursion_limit" is set to some number
    testdir.make_test(
        f"""
@schema.parametrize(stateful=Stateful.links, stateful_recursion_limit=1, method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    case.validate_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be 3 tests in total:
    # 1 - the original one for POST /users
    # 2 - stateful ones:
    #   - POST /users -> GET /users
    #   - POST /users -> PATCH /users
    result = testdir.run_and_assert("-v", passed=3)
    result.stdout.re_match_lines(
        [
            r"test_stateful_enabled_limit.py::test_\[POST:/api/users/\] PASSED",
            r"test_stateful_enabled_limit.py::test_\[POST:/api/users/ -> GET:/api/users/{user_id}\] PASSED * \[ 66%\]",
            r"test_stateful_enabled_limit.py::test_\[POST:/api/users/ -> PATCH:/api/users/{user_id}\] PASSED * \[100%\]",
            r"Hypothesis calls: 6",
        ]
    )


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_disabled(testdir, app_schema, openapi3_base_url):
    # When "stateful" is NOT used in the "parametrize" decorator
    testdir.make_test(
        f"""
@schema.parametrize(method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    case.validate_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be 1 test in total - the original one for POST /users
    result = testdir.run_and_assert("-v", passed=1)
    result.stdout.re_match_lines(
        [
            r"test_stateful_disabled.py::test_\[POST:/api/users/\] PASSED",
            r"Hypothesis calls: 2",
        ]
    )


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_not_used(testdir, app_schema):
    # When "stateful" is used in the "parametrize" decorator
    # And the test doesn't use "Case.call" or "Case.store_response" to store the feedback
    testdir.make_test(
        """
@schema.parametrize(stateful=Stateful.links, method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
        """,
        schema=app_schema,
    )
    # Then there should be 1 test in total - the original one for POST /users
    result = testdir.run_and_assert("-v", passed=1)
    result.stdout.re_match_lines(
        [
            r"test_stateful_not_used.py::test_\[POST:/api/users/\] PASSED",
            r"Hypothesis calls: 2",
        ]
    )
    # And a warning should be risen because responses were not stored
    assert f"  test_stateful_not_used.py:10: PytestWarning: {NOT_USED_STATEFUL_TESTING_MESSAGE}" in result.stdout.lines


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_store_response_without_stateful(testdir, app_schema, openapi3_base_url):
    # When "stateful" is NOT used in the "parametrize" decorator
    # And "case.store_response" is used inside the test
    testdir.make_test(
        f"""
@schema.parametrize(method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    with pytest.raises(RuntimeError, match="{MISSING_STATEFUL_ARGUMENT_MESSAGE}"):
        case.store_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be an exception, that is verified via the pytest.raises ctx manager
    testdir.run_and_assert(passed=1)


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_no_warning_on_failure(testdir, app_schema):
    # When a stateful test fails
    testdir.make_test(
        f"""
@schema.parametrize(stateful=Stateful.links, method="POST")
@settings(max_examples=2)
def test_(case):
    1 / 0
        """,
        schema=app_schema,
    )
    # Then there should be no warning about not using the "stateful" argument
    result = testdir.run_and_assert(failed=1)
    warning_text = f"  test_no_warning_on_failure.py:10: PytestWarning: {NOT_USED_STATEFUL_TESTING_MESSAGE}"
    assert warning_text not in result.stdout.lines
