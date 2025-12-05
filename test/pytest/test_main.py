import platform

import pytest

from schemathesis.generation.modes import GenerationMode


def test_pytest_parametrize_fixture(testdir):
    # When `pytest_generate_tests` is used on a module level for fixture parametrization
    testdir.make_test(
        """
from hypothesis import settings, HealthCheck


def pytest_generate_tests(metafunc):
    metafunc.parametrize("inner", ("A", "B"))

@pytest.fixture()
def param(inner):
    return inner * 2

@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_(request, param, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.path == "/users"
    assert case.method in ("GET", "POST")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # And there are multiple method/path combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be method/path combos x parameters in `pytest_generate_tests`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize_fixture.py::test_\[GET /users\]\[A\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[GET /users\]\[B\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[POST /users\]\[A\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[POST /users\]\[B\] PASSED",
            r"Hypothesis calls: 8",
        ]
    )


def test_missing_base_url_error_message(testdir):
    testdir.make_test(
        """
schema = schemathesis.openapi.from_dict(raw_schema)

@schema.parametrize()
def test_a(case):
    case.call()

@schema.parametrize()
def test_b(case):
    case.call_and_validate()
"""
    )
    result = testdir.runpytest("-v", "-s")
    assert "The `base_url` argument is required when specifying a schema via a file" in result.stdout.str()
    # But it should NOT include reproduction code since this is a configuration error, not an API failure
    assert "Reproduce with:" not in result.stdout.str()


def test_pytest_parametrize_class_fixture(testdir):
    # When `pytest_generate_tests` is used on a class level for fixture parametrization
    testdir.make_test(
        """
from hypothesis import settings, HealthCheck


class TestAPI:

    def pytest_generate_tests(self, metafunc):
        metafunc.parametrize("inner", ("A", "B"))

    @pytest.fixture()
    def param(self, inner):
        return inner * 2

    @schema.parametrize()
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_(self, request, param, case):
        request.config.HYPOTHESIS_CASES += 1
        assert case.operation.path == "/users"
        assert case.method in ("GET", "POST")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # And there are multiple method/path combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be method/path combos x parameters in `pytest_generate_tests`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[GET /users\]\[A\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[GET /users\]\[B\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[POST /users\]\[A\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[POST /users\]\[B\] PASSED",
            r"Hypothesis calls: 8",
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
    result = testdir.runpytest("-Werror", "--asyncio-mode=strict")
    # There should be no errors. There are no warnings from Schemathesis pytest plugin.
    result.assert_outcomes(passed=3)


def test_default_hypothesis_deadline(testdir):
    testdir.make_test(
        """
@schema.parametrize()
def test_a(case):
    assert settings().deadline is None

@schema.parametrize()
@settings(max_examples=5)
def test_b(case):
    assert settings().deadline is None

@schema.parametrize()
@settings(max_examples=5, deadline=100)
def test_c(case):
    assert settings().deadline.microseconds == 100 * 1000

def test_d():
    deadline = settings().deadline
    # E.g. changed globally via a custom profile in Schemathesis' test suite
    if deadline is not None:
        assert deadline.microseconds == 200 * 1000
""",
    )
    # When there is a test with Pytest
    result = testdir.runpytest()
    # Then it should use the global Schemathesis deadline for Hypothesis (None)
    result.assert_outcomes(passed=4)


def test_schema_given(testdir):
    # When the test uses `schema.given`
    testdir.make_test(
        """
from hypothesis.strategies._internal.core import DataObject

OPERATIONS = []

@schema.parametrize()
@schema.given(data=st.data())
def test_a(data, case):
    assert isinstance(data, DataObject)
    OPERATIONS.append(f"{case.method} {case.path}")


def teardown_module(module):
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
    result.assert_outcomes(passed=2)


def test_given_no_arguments(testdir):
    # When `schema.given` is used without arguments
    testdir.make_test(
        """
@schema.parametrize()
@schema.given()
def test(case):
    pass
        """,
    )
    # Then the wrapped test should fail with an error
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([".+given must be called with at least one argument"])


def test_given_with_explicit_examples(testdir):
    # When `schema.given` is used for a schema with explicit examples
    testdir.make_test(
        """
@schema.include(method="GET").parametrize()
@schema.given(data=st.data())
def test(case, data):
    pass
        """,
        paths={
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "anyKey",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "header0",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    # Then collection should fail with a clear error
    result = testdir.runpytest("-v")
    result.assert_outcomes(errors=1)
    stdout = result.stdout.str()
    assert "Cannot combine `@schema.given()` with schema examples" in stdout
    assert "Your test uses `@schema.given()` with custom strategies for: data" in stdout
    assert "extra parameters to your test function that schema examples cannot provide values for" in stdout
    assert "Solution: Create separate test functions" in stdout


def test_given_with_explicit_hypothesis_example(testdir):
    # When user explicitly adds @hypothesis.example() decorator with @schema.given()
    testdir.make_test(
        """
from hypothesis import example

@schema.parametrize()
@schema.given(user_id=st.integers())
@example(case=None)  # User explicitly adds @example
def test(case, user_id):
    pass
        """,
    )
    # Then collection should fail with an error (actually caught at collection now by builder.py)
    # because we're detecting missing strategies for user_id in the @example
    result = testdir.runpytest("-v")
    # Could be errors=1 (collection error) or failed=1 (runtime error from plugin.py)
    assert result.ret != 0
    stdout = result.stdout.str()
    # Should show error about inconsistent args
    assert "Inconsistent" in stdout or "example" in stdout.lower()


def test_given_with_schema_examples_in_request_body(testdir):
    # See GH-3328
    # When @schema.given() is combined with schema examples in request body
    testdir.makepyfile(
        """
import schemathesis
from hypothesis import strategies as st
import pytest


@pytest.fixture(scope="session")
def api_schema():
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "My API", "version": "1.0.0"},
        "paths": {
            "/fci": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "example": {
                                        "value": "st"
                                    },
                                    "properties": {
                                        "value": {
                                            "description": "The value"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    }

    schema = schemathesis.openapi.from_dict(schema_dict)
    schema.config.update(base_url="http://192.168.1.4/api")

    return schema


schema = schemathesis.pytest.from_fixture("api_schema")

existing_user_ids = [1, 42, 123, 456]


@schema.given(user_id=st.sampled_from(existing_user_ids))
@schema.parametrize()
def test_user_endpoints(case, user_id):
    if "user_id" in case.path_parameters:
        case.path_parameters["user_id"] = user_id
    case.call_and_validate()
        """
    )
    # Then the test should fail with a clear, actionable error message
    result = testdir.runpytest("-v")
    result.assert_outcomes(failed=1)
    stdout = result.stdout.str()
    assert "Cannot combine `@schema.given()` with schema examples" in stdout
    assert "Your test uses `@schema.given()` with custom strategies for: user_id" in stdout
    assert "extra parameters to your test function that schema examples cannot provide values for" in stdout
    assert "Solution: Create separate test functions" in stdout


def test_given_no_override(testdir):
    # When `schema.given` is used multiple times on the same test
    testdir.make_test(
        """
@schema.parametrize()
@schema.given(st.booleans())
@schema.given(st.booleans())
def test(case):
    pass
        """,
    )
    # Then the wrapped test should fail with an error
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([".+You have applied `given` to the `test` test more than"])


def test_parametrize_no_override(testdir):
    # When `schema.parametrize` is used multiple times on the same test
    testdir.make_test(
        """
@schema.parametrize()
@schema.parametrize()
def test(case):
    pass
        """,
    )
    # Then the wrapped test should fail with an error
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([".+You have applied `parametrize` to the `test` test more than"])


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
schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="failure").parametrize()
def test(case):
    case.call_and_validate()
    """,
        paths={"/failure": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    # Then there should be a helpful message in the output
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    assert "Reproduce with" in result.stdout.str()
    assert "Undocumented HTTP status code" in result.stdout.str()


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_fail_on_operations_with_infinite_recursive_references(testdir, schema_with_recursive_references):
    # When the test schema contains infinite recursive references
    testdir.make_test(
        """
@schema.parametrize()
def test(case):
    pass""",
        schema=schema_with_recursive_references,
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-rs")
    # Then this test should be failed with a proper error message
    result.assert_outcomes(failed=1)
    assert "Schema `#/components/schemas/Node` has a required reference to itself" in result.stdout.str()


def test_checks_as_a_list(testdir, openapi3_base_url):
    # When the user passes a list of checks instead of a tuple
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

def my_check(ctx, response, case):
    note("CHECKING!")

@schema.parametrize()
def test(case):
    response = case.call()
    case.validate_response(response, checks=(my_check,), additional_checks=[my_check])
""",
    )
    result = testdir.runpytest("-s")
    # Then it should work
    result.assert_outcomes(passed=1)
    assert "CHECKING!" in result.stdout.str()


def test_excluded_checks(testdir, openapi3_base_url):
    # When the user would like to exclude a check
    testdir.make_test(
        f"""
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import status_code_conformance, positive_data_acceptance

schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="failure").parametrize()
def test(case):
    response = case.call()
    case.validate_response(response, excluded_checks=(status_code_conformance, not_a_server_error, positive_data_acceptance))
""",
        paths={"/failure": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    result = testdir.runpytest()
    # We should skip checking for a server error
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("raise AssertionError", "Custom check failed: `my_check`"),
        ("raise AssertionError('My message')", "My message"),
    ],
)
def test_failing_custom_check(testdir, openapi3_base_url, body, expected):
    # When the user passes a custom check that fails
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

def my_check(ctx, response, case):
    {body}

def another_check(ctx, response, case):
    raise AssertionError("Another check")

@schema.parametrize()
def test(case):
    response = case.call()
    case.validate_response(response, checks=(my_check, another_check))
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)
    # Then the failure message should be displayed
    stdout = result.stdout.str()
    assert expected in stdout
    # And other failing checks are not ignored
    assert "Another check" in stdout


def test_no_collect_warnings(testdir):
    testdir.make_test(
        """
from schemathesis import *
    """,
    )
    result = testdir.runpytest()
    assert "cannot collect test class" not in result.stdout.str()


def test_skip_negative_without_parameters(testdir):
    # See GH-1463
    # When an endpoint has no parameters to negate
    testdir.make_test(
        """
schema = schemathesis.openapi.from_dict(raw_schema)
schema.config.generation.update(modes=[GenerationMode.NEGATIVE])

@schema.parametrize()
def test_(case):
    pass
""",
    )
    # Then it should be skipped
    result = testdir.runpytest("-v", "-rs", "-s")
    result.assert_outcomes(skipped=1)
    result.stdout.re_match_lines([r".*Impossible to generate negative test cases.*"])


def test_skip_impossible_to_negate(testdir):
    # See GH-1463
    # When endpoint's body schema can't be negated
    testdir.make_test(
        """
schema = schemathesis.openapi.from_dict(
    raw_schema
).include(method="POST")
schema.config.generation.update(modes=[GenerationMode.NEGATIVE])

@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
""",
        paths={
            "/pets": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {},
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    # Then it should be skipped
    result = testdir.runpytest("-v", "-rs", "-s")
    result.assert_outcomes(skipped=1)
    result.stdout.re_match_lines([r".*Impossible to generate negative test cases.*"])


def test_do_not_skip_partially_negated(testdir):
    # When endpoint's body schema can't be negated
    # And there is another parameter that can be negated
    testdir.make_test(
        """
schema = schemathesis.openapi.from_dict(
    raw_schema
).include(method="POST")
schema.config.generation.update(modes=[GenerationMode.NEGATIVE])

@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/pets": {
                "post": {
                    "parameters": [{"in": "query", "name": "key", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {},
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    # Then it should NOT be skipped
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


@pytest.mark.parametrize("location", ["header", "cookie", "query"])
def test_path_parameters_allow_partial_negation(testdir, location):
    # If path parameters can not be negated and other parameters can be negated
    testdir.make_test(
        """
schema = schemathesis.openapi.from_dict(
    raw_schema
).include(method="GET", path_regex="/pets/{key}/")
schema.config.generation.update(modes=[GenerationMode.NEGATIVE])

@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/pets/{key}/": {
                "get": {
                    "parameters": [
                        {"in": "path", "name": "key", "required": True, "schema": {}},
                        {"in": location, "name": "foo", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    # Then non-negated should be generated as positive
    # And the ones that can be negated should be negated
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_many_path_parameters_allow_partial_negation(testdir):
    # If just one path parameter can not be negated and other parameters can be negated
    testdir.make_test(
        """
schema = schemathesis.openapi.from_dict(
    raw_schema
).include(
    method="GET",
    path_regex="/pets/{key}/{value}/",
)
schema.config.generation.update(modes=[GenerationMode.NEGATIVE])

@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/pets/{key}/{value}/": {
                "get": {
                    "parameters": [
                        {"in": "path", "name": "key", "required": True, "schema": {}},
                        {"in": "path", "name": "value", "required": True, "schema": {"type": "integer"}},
                        {"in": "query", "name": "foo", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    # Then non-negated should be generated as positive
    # And the ones that can be negated should be negated
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_trimmed_output(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

@schema.parametrize()
def test_a(case):
    case.call_and_validate()

@given(st.integers())
def test_b(v):
    1 / v""",
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=2)
    stdout = result.stdout.str()
    # Internal Schemathesis' frames should not appear in the output
    assert "def validate_response(" not in stdout
    # And Hypothesis "Falsifying example" block is not in the output of Schemathesis' tests
    assert "Falsifying example: test_a(" not in stdout
    # And regular Hypothesis tests have it
    assert "Falsifying example: test_b(" in stdout


def test_stateful_missing_links_hide_hypothesis_block(testdir):
    testdir.make_test(
        """
import pytest


@pytest.fixture
def api_schema():
    # Mirrors docs/guides/stateful-testing.py fixture example
    schema = schemathesis.openapi.from_dict(raw_schema)
    schema.config.phases.update(phases=["stateful"])
    return schema


@pytest.fixture
def state_machine(api_schema):
    return api_schema.as_state_machine()


def test_statefully(state_machine):
    state_machine.run()
""",
        paths={
            "/users": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                    }
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-k", "test_statefully")
    result.assert_outcomes(failed=1)
    stdout = result.stdout.str()
    assert "Schema contains no link definitions required for stateful testing" in stdout
    assert "Falsifying example" not in stdout


def test_invalid_schema_reraising(testdir):
    # When there is a non-Schemathesis test failing because of Hypothesis' `InvalidArgument` error
    testdir.make_test(
        """
@given(st.integers(min_value=5, max_value=4))
def test(value):
    pass""",
    )
    # Then it should not be re-raised as `InvalidSchema`
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    assert "InvalidSchema: Cannot have" not in result.stdout.str()


@pytest.mark.parametrize("value", [True, False])
def test_output_sanitization(testdir, openapi3_base_url, value):
    auth = "secret-auth"
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="failure").parametrize()
def test(case):
    case.call_and_validate(headers={{'Authorization': '{auth}'}})
""",
        paths={"/failure": {"get": {"responses": {"200": {"description": "OK"}}}}},
        sanitize_output=value,
    )
    result = testdir.runpytest()
    # We should skip checking for a server error
    result.assert_outcomes(failed=1)
    if value:
        expected = rf"curl -X GET -H 'Authorization: [Filtered]' {openapi3_base_url}/failure"
    else:
        expected = rf"curl -X GET -H 'Authorization: {auth}' {openapi3_base_url}/failure"
    assert expected in result.stdout.str()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.operations("basic")
def test_output_sanitization_via_config_file(testdir, openapi3_schema_url, enabled):
    testdir.make_test(
        f"""
config = schemathesis.Config.from_dict({{
    "headers": {{"Authorization": "secret"}},
    "output": {{
        "sanitization": {{"enabled": {enabled}}}
    }}
}})
schema = schemathesis.openapi.from_url('{openapi3_schema_url}', config=config)
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.include(name="GET /basic").parametrize()
def test(case):
    case.call_and_validate()
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    if enabled:
        assert "Authorization: [Filtered]" in result.stdout.str()
    else:
        assert "Authorization: secret" in result.stdout.str()


def extract_hypothesis_error(text):
    lines = text.split("\n")
    result_lines = []
    capturing = False

    for line in lines:
        if "E   hypothesis" in line:
            capturing = True

        if capturing:
            if line.startswith("All traceback"):
                break
            result_lines.append(line)

    return "\n".join(result_lines)


@pytest.mark.operations("unsatisfiable")
def test_unsatisfiable_schema(testdir, openapi3_schema_url, snapshot):
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.parametrize()
def test(case):
    case.call_and_validate()
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    assert extract_hypothesis_error(result.stdout.str()) == snapshot


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires a more complex test setup")
@pytest.mark.operations("unsatisfiable")
def test_health_check_failure(ctx, testdir, openapi3_base_url, snapshot):
    schema_path = ctx.openapi.write_schema(
        {
            "/items/{item_id}/": {
                "patch": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}},
                        "required": True,
                    }
                }
            }
        },
        components={
            "schemas": {
                "Item": {
                    "type": "string",
                    "format": "date-time",
                    "pattern": "abc",
                }
            }
        },
    )

    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_path('{schema_path}')
schema.config.generation.update(modes=[GenerationMode.POSITIVE])
schema.config.base_url = '{openapi3_base_url}'

@schema.parametrize()
def test(case):
    pass
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    assert extract_hypothesis_error(result.stdout.str()) == snapshot


def test_unsatisfiable_example(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.phases.coverage.enabled = False
schema.config.phases.fuzzing.enabled = False

@schema.include(path_regex="success").parametrize()
@settings(phases=[Phase.explicit])
def test(case):
    pass
""",
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        # This parameter is not satisfiable
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 5, "maximum": 4},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "foo": {"type": "string", "example": "foo example string"},
                                    },
                                },
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    # We should skip checking for a server error
    result.assert_outcomes(failed=1)
    assert (
        "hypothesis.errors.Unsatisfiable: Failed to generate test cases from examples for this API operation"
        in result.stdout.str()
    )


def test_unsupported_regex_pattern_removed(testdir, openapi3_base_url):
    # When a schema contains an unsupported regex pattern, it is removed and tests can proceed
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="success").parametrize()
@settings(phases=[Phase.explicit, Phase.generate])
def test(case):
    pass
""",
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "region": {
                                            "nullable": True,
                                            "pattern": "^[\\w\\s\\-\\/\\pL,.#;:()']+$",
                                            "type": "string",
                                        },
                                    },
                                    "required": ["region"],
                                    "type": "object",
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest()
    # Test passes because the unsupported pattern is removed during schema conversion
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "phases",
    ["Phase.explicit", "Phase.explicit, Phase.generate"],
)
def test_invalid_header_in_example(testdir, openapi3_base_url, phases):
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="success").parametrize()
@settings(phases=[{phases}])
def test(case):
    pass
 """,
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "SESSION",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "integer"},
                            "example": "test\ntest",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    assert "Failed to generate test cases from examples for this API" in result.stdout.str()


def test_coverage_phase(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("image/jpeg", st.just(b""))
schema.config.update(base_url="{openapi3_base_url}")
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

@schema.include(path_regex="success").parametrize()
def test(case):
    case.call()
""",
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "image/jpeg": {
                                "schema": {"format": "base64", "type": "string"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_non_serializable_example(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="success").parametrize()
@settings(phases=[Phase.explicit])
def test(case):
    case.call()
""",
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "image/jpeg": {
                                "schema": {"format": "base64", "type": "string"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    # We should skip checking for a server error
    result.assert_outcomes(failed=1)
    assert (
        "Failed to generate test cases from examples for this API operation because of unsupported payload media types"
        in result.stdout.str()
    )


@pytest.mark.operations("path_variable", "custom_format")
def test_override(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
schema.config.update(parameters={{"key": "foo", "id": "bar"}})

@schema.include(path_regex="path_variable|custom_format").parametrize()
def test(case):
    if not hasattr(case.meta.phase.data, "description"):
        if "key" in case.operation.path_parameters:
            assert case.path_parameters["key"] == "foo"
            assert "id" not in (case.query or {{}}), "`id` is present"
        if "id" in case.operation.query:
            assert case.query["id"] == "bar"
            assert "key" not in (case.path_parameters or {{}}), "`key` is present"
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=2)


@pytest.mark.operations("csv_payload")
def test_error_reporting(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url('{openapi3_schema_url}')

@schema.include(path_regex="csv").parametrize()
def test(case):
    pass
"""
    )
    result = testdir.runpytest()
    assert "while generating" not in result.stdout.str()


@pytest.mark.operations("missing_path_parameter")
def test_missing_path_parameter(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url('{openapi3_schema_url}')

@schema.parametrize()
@settings(max_examples=3)
def test(case):
    if "id" not in case.path_parameters:
        pytest.skip("Test should normally fail")
    case.call_and_validate()
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)


@pytest.mark.operations("failure")
def test_disable_checks_via_config(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
config = schemathesis.Config.from_dict({{
    "checks": {{
        "not_a_server_error": {{"enabled": False}},
        "content_type_conformance": {{"enabled": False}},
    }}
}})
schema = schemathesis.openapi.from_url('{openapi3_schema_url}', config=config)

@schema.include(name="GET /failure").parametrize()
def test(case):
    case.call_and_validate()
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    ["filter", "expected"],
    (
        ("", {"failed": 1}),
        # Adds two additional operations
        (".include(name_regex='GET /(success|text)')", {"failed": 2, "passed": 1}),
        # Also excludes the one defined in the config file
        (".include(name_regex='GET /(success|text)').exclude(name='POST /write_only')", {"failed": 1, "passed": 1}),
    ),
)
@pytest.mark.operations("__all__")
def test_filter_combination(testdir, openapi3_schema_url, filter, expected):
    testdir.make_test(f"""
config = schemathesis.Config.from_dict({{
    "operations": [{{
        "exclude-name": "POST /write_only",
        "enabled": False,
    }}]
}})
schema = schemathesis.openapi.from_url("{openapi3_schema_url}", config=config)

@schema{filter}.parametrize()
def test_api(case):
    case.call_and_validate()
    """)
    result = testdir.runpytest()
    result.assert_outcomes(**expected)


def test_transport_kwargs_from_config(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
config = schemathesis.Config.from_dict({{
    "tls-verify": False,
    "headers": {{ "X-Foo": "Bar", "X-Spam": "Unknown" }},
    "request-timeout": 3,
    "request-cert": "foo",
    "phases": {{
        "coverage": {{
            "enabled": False,
        }}
    }}
}})
schema = schemathesis.openapi.from_url('{openapi3_schema_url}', config=config)

def noop(*args, **kwargs):
    pass

@schema.include(name="GET /success").parametrize()
@settings(suppress_health_check=list(HealthCheck))
def test(case, mocker):
    spy = mocker.patch("requests.Session.request")
    case.call_and_validate(checks=[noop], headers={{"X-Spam": "Explicit" }})
    kwargs = spy.call_args[1]
    assert kwargs["verify"] is False
    assert kwargs["timeout"] == 3
    assert kwargs["cert"] == "foo"
    assert kwargs["headers"]["X-Foo"] == "Bar"
    assert kwargs["headers"]["X-Spam"] == "Explicit"
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_config_using_headers(testdir):
    testdir.make_test(
        """
raw_schema = {
    "openapi": "3.1.0",
    "paths": {
        "/bookings": {
            "post": {
                "parameters": [
                    {
                        "name": "authorization",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
    },
}
schema = schemathesis.openapi.from_dict(raw_schema)
HEADERS = {"Authorization": "Bearer secret-token"}
schema.config.update(headers=HEADERS)

@schema.parametrize()
def test(case):
    assert case.headers == HEADERS
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_config_using_auth(testdir):
    testdir.make_test(
        """
raw_schema = {
    "openapi": "3.1.0",
    "paths": {
        "/bookings": {
            "post": {
                "parameters": [
                    {
                        "name": "authorization",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
    },
}
schema = schemathesis.openapi.from_dict(raw_schema)
schema.config.update(basic_auth=("test", "test"))

@schema.parametrize()
def test(case):
    assert case.headers == {"Authorization": "Basic dGVzdDp0ZXN0"}
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_config_generation(testdir):
    MAX_EXAMPLES_A = 14
    MAX_EXAMPLES_B = 42
    testdir.make_test(
        f"""
raw_schema = {{
    "openapi": "3.1.0",
    "paths": {{
        "/bookings": {{
            "post": {{
                "parameters": [
                    {{
                        "name": "key",
                        "in": "query",
                        "required": False,
                        "schema": {{"type": "string"}},
                    }}
                ]
            }}
        }},
    }},
}}
schema = schemathesis.openapi.from_dict(raw_schema)
schema.config.generation.update(
    modes=[GenerationMode.POSITIVE],
    max_examples={MAX_EXAMPLES_A},
)

@schema.parametrize()
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1


@schema.parametrize()
@settings(max_examples={MAX_EXAMPLES_B})
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            rf"Hypothesis calls: {MAX_EXAMPLES_A + MAX_EXAMPLES_B + 2}",
        ]
    )


def test_csv_response_validation_direct(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
import requests
from schemathesis.core.transport import Response
from schemathesis.specs.openapi.checks import response_schema_conformance

schema.config.update(base_url="{openapi3_base_url}")
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.include(name="GET /success").parametrize()
def test_csv_validation(case):
    request = requests.Request('GET', "http://127.0.0.1/api/success").prepare()

    response = Response(
        status_code=200,
        headers={{}},
        content=b"name,age\\nJohn,25",
        request=request,
        elapsed=0.1,
        verify=False
    )
    case.validate_response(response, checks=(response_schema_conformance,))
""",
        paths={
            "/success": {
                "get": {
                    "parameters": [
                        {
                            "name": "format",
                            "in": "query",
                            "schema": {"type": "string", "enum": ["json", "csv"]},
                            "required": True,
                        }
                    ],
                    "responses": {
                        "default": {
                            "description": "Data response",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines(
        [
            r".*/api/success\?format=csv.*",
        ]
    )
    assert "generation/hypothesis/builder.py" not in result.stdout.str()
    assert "res = hook_impl.function(*args)" not in result.stdout.str()


@pytest.mark.operations("success")
def test_graphql_lazy_loading(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
import sys

schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
for k in list(sys.modules):
    if k.startswith("graphql"):
        del sys.modules[k]
assert "graphql" not in sys.modules

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    case.call_and_validate()
    assert "graphql" not in sys.modules
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_distinct_exceptions_not_deduplicated(testdir):
    # When different errors occur, they should not be deduplicated
    testdir.make_test(
        """
from hypothesis import Phase, settings

@schema.parametrize()
@settings(phases=[Phase.explicit])
def test(case):
    # Raise different errors for different methods to verify they're not deduplicated
    if case.method == "GET":
        raise ValueError("GET error")
    elif case.method == "POST":
        raise TypeError("POST error")
    else:
        raise RuntimeError("Other error")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
                "put": {"responses": {"200": {"description": "OK"}}},
            }
        },
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(failed=3)
    # Should show multiple distinct failures
    output = result.stdout.str()
    assert "test[GET /users] FAILED" in output
    assert "test[POST /users] FAILED" in output
    assert "test[PUT /users] FAILED" in output
    assert output.count("ValueError: GET error") == 1
    assert output.count("TypeError: POST error") == 1
    # This one happens in all 3 tests
    assert output.count("RuntimeError: Other error") == 3


def test_curl_generation_does_not_hide_main_error(testdir):
    # When curl generation fails, main error should still be shown (not replaced by curl error)
    testdir.makefile(
        ".yaml",
        schema="""
openapi: 3.0.0
info:
  title: Test
  version: 1.0.0
paths:
  /test/{id}}:
    parameters:
      - name: id
        in: path
        required: true
        schema:
          type: integer
    get:
      responses:
        '200':
          description: OK
""",
    )
    testdir.make_test(
        """
schema = schemathesis.openapi.from_path("schema.yaml")

@schema.parametrize()
def test(case):
    case.call_and_validate()
"""
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(failed=1)
    output = result.stdout.str()
    # Should show the main error (malformed path template)
    assert "InvalidSchema" in output
    assert "Malformed path template" in output
    # Should NOT show "Reproduce with:" since curl generation failed
    assert "Reproduce with:" not in output


def test_urlencoded_type_mutations_should_not_cause_false_positives(testdir):
    testdir.makepyfile(
        test_case="""
from flask import Flask, jsonify
import schemathesis
from hypothesis import settings

raw_schema = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "version": "1.0.0"},
    "paths": {
        "/success": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "my_param": {"type": "number"}
                                },
                                "additionalProperties": False
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "OK"}
                    }
                }
            }
        }
    }
}

app = Flask(__name__)

@app.route("/openapi.json")
def schema():
    return jsonify(raw_schema)

@app.route("/success", methods=["POST"])
def success():
    return jsonify({"success": True})

schema_obj = schemathesis.openapi.from_wsgi("/openapi.json", app)

@schema_obj.parametrize()
@settings(max_examples=20)
def test(case):
    pass
"""
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
