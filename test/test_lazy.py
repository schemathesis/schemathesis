import sys

import pytest

from schemathesis._dependency_versions import IS_PYRATE_LIMITER_ABOVE_3


def test_default(testdir):
    # When LazySchema is used
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(case):
    assert case.full_path == "/v1/users"
    assert case.method == "GET"
"""
    )
    result = testdir.runpytest("-v")
    # Then the generated test should use this fixture
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"test_default.py::test_ PASSED", r".*1 passed"])


def test_with_settings(testdir):
    # When hypothesis settings are applied to the test function
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@settings(phases=[])
@lazy_schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
"""
    )
    result = testdir.runpytest("-v", "-s")
    # Then settings should be applied to the test
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.re_match_lines([r"test_with_settings.py::test_ PASSED", r".*1 passed"])
    result.stdout.re_match_lines([r"Hypothesis calls: 0$"])


def test_invalid_operation(testdir, hypothesis_max_examples, is_older_subtests):
    # When the given schema is invalid
    # And schema validation is disabled
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/valid": {
                "get": {
                    "parameters": [{"type": "integer", "name": "id", "in": "query", "required": True}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/invalid": {
                "get": {
                    "parameters": [{"type": "int", "name": "id", "in": "query", "required": True}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        validate_schema=False,
    )
    result = testdir.runpytest("-v", "-rf")
    # Then one test should be marked as failed (passed - /users, failed /)
    result.assert_outcomes(passed=1, failed=1)
    if is_older_subtests:
        expected = [
            r"test_invalid_operation.py::test_\[GET /v1/valid\] PASSED *\[ 25%\]",
            r"test_invalid_operation.py::test_\[GET /v1/invalid\] FAILED *\[ 50%\]",
            r"test_invalid_operation.py::test_\[GET /v1/users\] PASSED *\[ 75%\]",
            r".*1 passed",
        ]
    else:
        expected = [
            r"test_invalid_operation.py::test_\[GET /v1/valid\] SUBPASS +\[ 25%\]",
            r"test_invalid_operation.py::test_\[GET /v1/invalid\] SUBFAIL +\[ 50%\]",
            r"test_invalid_operation.py::test_\[GET /v1/users\] SUBPASS +\[ 75%\]",
            r".*1 passed",
        ]
    result.stdout.re_match_lines(expected)
    # 100 for /valid, 1 for /users
    hypothesis_calls = (hypothesis_max_examples or 100) + 1
    result.stdout.re_match_lines([rf"Hypothesis calls: {hypothesis_calls}$"])


def test_with_fixtures(testdir):
    # When the test uses custom arguments for pytest fixtures
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@pytest.fixture
def another():
    return 1

@lazy_schema.parametrize()
def test_(request, case, another):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method == "GET"
    assert another == 1
"""
    )
    result = testdir.runpytest("-v")
    # Then the generated test should use these fixtures
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"test_with_fixtures.py::test_ PASSED", r".*1 passed"])
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_with_parametrize_filters(testdir):
    # When the test uses method / endpoint / tag / operation-id filter
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize(endpoint="/first")
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/first"

@lazy_schema.parametrize(method="POST")
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "POST"

@lazy_schema.parametrize(tag="foo")
def test_c(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/second"
    assert case.method == "GET"

@lazy_schema.parametrize(operation_id="updateThird")
def test_d(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/third"
    assert case.method == "PUT"
""",
        paths={
            "/first": {
                "post": {"tags": ["bar"], "responses": {"200": {"description": "OK"}}},
                "get": {"tags": ["baz"], "responses": {"200": {"description": "OK"}}},
            },
            "/second": {
                "post": {"responses": {"200": {"description": "OK"}}},
                "get": {"tags": ["foo"], "responses": {"200": {"description": "OK"}}},
            },
            "/third": {"put": {"operationId": "updateThird", "responses": {"200": {"description": "OK"}}}},
        },
        tags=[{"name": "foo"}, {"name": "bar"}],
    )
    result = testdir.runpytest("-v")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_with_parametrize_filters.py::test_a PASSED",
            r"test_with_parametrize_filters.py::test_b PASSED",
            r"test_with_parametrize_filters.py::test_c PASSED",
            r"test_with_parametrize_filters.py::test_d PASSED",
            r".*4 passed",
        ]
    )
    result.stdout.re_match_lines([r"Hypothesis calls: 6$"])


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Decorator syntax available from Python 3.9")
def test_with_parametrize_filters_override(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize(endpoint=None, method="GET")
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "GET"

@lazy_schema.include(path_regex="/second", method=None).parametrize()
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/second"

@lazy_schema.parametrize()
def test_c(request, case):
    request.config.HYPOTHESIS_CASES += 1

@lazy_schema.exclude(method=["post"]).parametrize()
def test_d(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/first": {
                "post": {"tags": ["foo"], "responses": {"200": {"description": "OK"}}},
                "get": {"tags": ["foo"], "responses": {"200": {"description": "OK"}}},
            },
            "/second": {
                "post": {"tags": ["foo"], "responses": {"200": {"description": "OK"}}},
                "get": {"tags": ["foo"], "responses": {"200": {"description": "OK"}}},
            },
            "/third": {
                "post": {"tags": ["bar"], "responses": {"200": {"description": "OK"}}},
                "get": {"tags": ["bar"], "responses": {"200": {"description": "OK"}}},
            },
        },
        method="POST",
        path="/first",
        tag="foo",
    )
    result = testdir.runpytest("-v", "-s")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_with_parametrize_filters_override.py::test_a PASSED",
            r"test_with_parametrize_filters_override.py::test_b PASSED",
            r"test_with_parametrize_filters_override.py::test_c PASSED",
            r"test_with_parametrize_filters_override.py::test_d PASSED",
            r".*4 passed",
        ]
    )
    # test_a: 2 = 2 GET to /first, /second
    # test_b: 2 = 1 GET + 1 POST to /second
    # test_c: 1 = 1 POST to /first
    # test_d: 1 = 1 POST to /first
    result.stdout.re_match_lines([r"Hypothesis calls: 6$"])


def test_with_schema_filters(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema", endpoint="/v1/pets", method="POST")

@lazy_schema.parametrize()
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/pets"
    assert case.method == "POST"
""",
        paths={"/pets": {"post": {"responses": {"200": {"description": "OK"}}}}},
    )
    result = testdir.runpytest("-v")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"test_with_schema_filters.py::test_a PASSED"])
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_with_schema_filters_override(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema", endpoint=None, method="POST")

@lazy_schema.parametrize()
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "POST"

lazy_schema = schemathesis.from_pytest_fixture("simple_schema", endpoint=None, method="POST")
@lazy_schema.parametrize(endpoint="/second", method=None)
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/second"
""",
        paths={
            "/first": {
                "post": {"responses": {"200": {"description": "OK"}}},
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/second": {
                "post": {"responses": {"200": {"description": "OK"}}},
                "get": {"responses": {"200": {"description": "OK"}}},
            },
        },
        method="GET",
        path="/first",
    )
    result = testdir.runpytest("-v", "-s")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"test_with_schema_filters_override.py::test_a PASSED",
            r"test_with_schema_filters_override.py::test_b PASSED",
            r".*2 passed",
        ]
    )
    # test_a: 2 = 1 POST to /first + 1 POST to /second
    # test_b: 2 = 1 GET to /second + 1 POST to /second
    result.stdout.re_match_lines([r"Hypothesis calls: 4$"])


def test_schema_filters_with_parametrize_override(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema", endpoint="/v1/first", method="POST")

@lazy_schema.parametrize(endpoint="/second", method="GET")
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/second"
    assert case.method == "GET"

@lazy_schema.parametrize(endpoint=None, method="GET")
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "GET"

@lazy_schema.parametrize(endpoint="/second", method=None)
def test_c(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/second"

@lazy_schema.parametrize()
def test_d(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/first"
    assert case.method == "POST"
""",
        paths={
            "/first": {
                "post": {"responses": {"200": {"description": "OK"}}},
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/second": {
                "post": {"responses": {"200": {"description": "OK"}}},
                "get": {"responses": {"200": {"description": "OK"}}},
            },
        },
    )
    result = testdir.runpytest("-v")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_schema_filters_with_parametrize_override.py::test_a PASSED",
            r"test_schema_filters_with_parametrize_override.py::test_b PASSED",
            r"test_schema_filters_with_parametrize_override.py::test_c PASSED",
            r"test_schema_filters_with_parametrize_override.py::test_d PASSED",
            r".*4 passed",
        ]
    )
    # test_a: 1 = 1 GET to /first
    # test_b: 3 = 3 GET to /users, /first, /second
    # test_c: 2 = 1 GET + 1 POST to /second
    # test_d: 1 = 1 POST to /first
    result.stdout.re_match_lines([r"Hypothesis calls: 7$"])


def test_invalid_fixture(testdir):
    # When the test uses a schema fixture that doesn't return a BaseSchema subtype
    testdir.make_test(
        """
@pytest.fixture
def bad_schema():
    return 1

lazy_schema = schemathesis.from_pytest_fixture("bad_schema")

@lazy_schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
"""
    )
    result = testdir.runpytest("-v")
    # Then the generated test should use these fixtures
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines(
        [
            r"test_invalid_fixture.py::test_ FAILED",
            ".*ValueError: The given schema must be an instance of BaseSchema, got: <class 'int'>",
            r".*1 failed",
        ]
    )


def test_get_request_with_body(testdir, schema_with_get_payload):
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        schema=schema_with_get_payload,
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines([r"E +BodyInGetRequestError: GET requests should not contain body parameters."])


@pytest.mark.parametrize(
    "decorators",
    (
        """@lazy_schema.hooks.apply(before_generate_headers)
@lazy_schema.parametrize()""",
        """@lazy_schema.parametrize()
@lazy_schema.hooks.apply(before_generate_headers)""",
    ),
)
def test_hooks_with_lazy_schema(testdir, simple_openapi, decorators):
    testdir.make_test(
        f"""
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.hook
def before_generate_query(context, strategy):
    return strategy.filter(lambda x: x["id"].isdigit())

def before_generate_headers(context, strategy):
    def convert(x):
        x["value"] = "cool"
        return x
    return strategy.map(convert)

{decorators}
@settings(max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.query["id"].isdigit()
    assert case.headers["value"] == "cool"
""",
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines(["Hypothesis calls: 5"])


@pytest.mark.parametrize("given", ("data=st.data()", "st.data()"))
def test_schema_given(testdir, given):
    # When the schema is defined via a pytest fixture
    # And `schema.given` is used
    testdir.make_test(
        f"""
from hypothesis.strategies._internal.core import DataObject

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")
OPERATIONS = []

@lazy_schema.parametrize()
@lazy_schema.given({given})
def test_a(data, case):
    assert isinstance(data, DataObject)
    OPERATIONS.append(f"{{case.method}} {{case.path}}")

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
    # And the total number of passed tests is 1: one high-level test with multiple subtests
    result.assert_outcomes(passed=1)


def test_invalid_given_usage(testdir):
    # When `schema.given` is used incorrectly (e.g. called without arguments)
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
@lazy_schema.given()
def test(case):
    pass
        """,
    )
    # Then the wrapped test should fail with an error
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([".+given must be called with at least one argument"])


def test_override_base_url(testdir):
    # When `base_url` is passed to `from_pytest_fixture`
    testdir.make_test(
        """
schema.base_url = "http://127.0.0.1/a1"
lazy_schema = schemathesis.from_pytest_fixture("simple_schema", base_url="http://127.0.0.1/a2")

@lazy_schema.parametrize()
def test_a(case):
    assert schema.base_url == "http://127.0.0.1/a1"
    assert case.operation.schema.base_url == "http://127.0.0.1/a2"

lazy_schema2 = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema2.parametrize()
def test_b(case):
    # No override
    assert schema.base_url == case.operation.schema.base_url == "http://127.0.0.1/a1"
        """,
    )
    # Then it should be overridden in the resulting schema
    # And the original one should remain the same
    result = testdir.runpytest()
    result.assert_outcomes(passed=2)


@pytest.mark.parametrize("settings", ("", "@settings(deadline=None)"))
def test_parametrized_fixture(testdir, openapi3_base_url, is_older_subtests, settings):
    # When the used pytest fixture is parametrized via `params`
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"

@pytest.fixture(params=["a", "b"])
def parametrized_lazy_schema(request):
    return schema

lazy_schema = schemathesis.from_pytest_fixture("parametrized_lazy_schema")

@lazy_schema.parametrize()
{settings}
def test_(case):
    case.call()
""",
    )
    result = testdir.runpytest("-v")
    # Then tests should be parametrized as usual
    result.assert_outcomes(passed=2)
    if is_older_subtests:
        expected = [
            r"test_parametrized_fixture.py::test_\[a\]\[GET /api/users\] PASSED",
            r"test_parametrized_fixture.py::test_\[b\]\[GET /api/users\] PASSED",
        ]
    else:
        expected = [
            r"test_parametrized_fixture.py::test_\[a\]\[GET /api/users\] SUBPASS",
            r"test_parametrized_fixture.py::test_\[b\]\[GET /api/users\] SUBPASS",
        ]
    result.stdout.re_match_lines(expected)


def test_data_generation_methods(testdir, is_older_subtests):
    # When data generation method config is specified on the schema which is wrapped by a lazy one
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    return schemathesis.from_dict(raw_schema, data_generation_methods=schemathesis.DataGenerationMethod.all())

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
""",
        paths={
            "/users": {
                "get": {
                    "parameters": [{"in": "query", "name": "key", "required": True, "type": "integer"}],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
    )
    testdir.makepyfile(
        conftest="""
from _pytest.config import hookimpl


@hookimpl(hookwrapper=True)
def pytest_terminal_summary(terminalreporter) -> None:
    reports = [
        report
        for report in terminalreporter.stats["passed"]
        if hasattr(report, "context")
    ]
    unique = {
        tuple(report.context.kwargs.items())
        for report in reports
    }
    # SubTest reports should contain unique kwargs
    assert len(unique) == len(reports) == 1
    yield
"""
    )
    # Then it should be taken into account
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)  # It is still a single test on the top level
    # And it should be the same test in the end
    message = r"test_data_generation_methods.py::test_\[GET /v1/users\] "
    if is_older_subtests:
        message += "PASSED"
    else:
        message += "SUBPASS"
    result.stdout.re_match_lines([message])


def test_data_generation_methods_override(testdir, is_older_subtests):
    # When data generation method config is specified on the schema which is wrapped by a lazy one
    # And then overridden on the` from_pytest_fixture` level
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    return schemathesis.from_dict(raw_schema, data_generation_methods=schemathesis.DataGenerationMethod.all())

lazy_schema = schemathesis.from_pytest_fixture(
    "api_schema",
    data_generation_methods=schemathesis.DataGenerationMethod.positive
)

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
""",
    )
    # Then the overridden one should be used
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)
    if is_older_subtests:
        expected = r"test_data_generation_methods_override.py::test_\[GET /v1/users\] PASSED *\[ 50%\]"
    else:
        expected = r"test_data_generation_methods_override.py::test_\[GET /v1/users\] SUBPASS *\[ 50%\]"
    result.stdout.re_match_lines([expected])


def test_hooks_are_merged(testdir):
    # When the wrapped schema has hooks
    # And the lazy schema also has hooks
    testdir.make_test(
        """
COUNTER = 1

def before_generate_case_first(ctx, strategy):

    def change(case):
        global COUNTER
        if case.headers is None:
            case.headers = {}
        case.headers["one"] = COUNTER
        COUNTER += 1
        return case

    return strategy.map(change)

@pytest.fixture()
def api_schema():
    loaded = schemathesis.from_dict(raw_schema)
    loaded.hook("before_generate_case")(before_generate_case_first)
    return loaded


lazy_schema = schemathesis.from_pytest_fixture("api_schema")

def before_generate_case_second(ctx, strategy):

    def change(case):
        global COUNTER
        if case.headers is None:
            case.headers = {}
        case.headers["two"] = COUNTER
        COUNTER += 1
        return case

    return strategy.map(change)

lazy_schema.hook("before_generate_case")(before_generate_case_second)

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_(case):
    assert case.headers == {"one": 1, "two": 2}
    """,
    )
    # Then all hooks should be merged
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_error_on_no_matches(testdir):
    # When test filters don't match any operation
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    return schemathesis.from_dict(raw_schema)

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize(operation_id=["does-not-exist"])
@settings(max_examples=1)
def test_(case):
    pass
""",
    )
    result = testdir.runpytest("-v")
    # Then it should be a failure (the collection phase is done, so it can't be an error)
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines(
        [
            r"E *Failed: Test function test_error_on_no_matches.py::test_ does not "
            r"match any API operations and therefore has no effect"
        ]
    )


@pytest.mark.parametrize(
    "decorators",
    (
        """@schema.parametrize()
@pytest.mark.acceptance""",
        """@pytest.mark.acceptance
@schema.parametrize()""",
    ),
)
def test_marks_transfer(testdir, decorators):
    # See GH-1378
    # When a pytest mark decorator is applied
    testdir.make_test(
        f"""
@pytest.fixture
def web_app():
    1 / 0

schema = schemathesis.from_pytest_fixture("web_app")

{decorators}
def test_schema(case):
    1 / 0
    """
    )
    result = testdir.runpytest("-m", "not acceptance")
    # Then deselecting by a mark should work
    result.assert_outcomes()


def test_skip_negative_without_parameters(testdir, is_older_subtests):
    # See GH-1463
    # When an endpoint has no parameters to negate
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    return schemathesis.from_dict(raw_schema, data_generation_methods=schemathesis.DataGenerationMethod.negative)

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize()
def test_(case):
    pass
""",
    )
    # Then it should be skipped
    result = testdir.runpytest("-v", "-rs")
    result.assert_outcomes(passed=1, skipped=1)
    if is_older_subtests:
        expected = [r".* SKIPPED .*"]
    else:
        expected = [r".* SUBSKIP .*"]
    expected.append(r".*It is not possible to generate negative test cases.*")
    result.stdout.re_match_lines(expected)


def test_trimmed_output(testdir):
    # When `from_pytest_fixture` is used
    # And tests are failing
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(case):
    1 / 0""",
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1, failed=1)
    stdout = result.stdout.str()
    # Internal Schemathesis' frames should not appear in the output
    assert "def run_subtest" not in stdout


@pytest.mark.operations("multiple_failures")
def test_multiple_failures(testdir, openapi3_schema_url):
    # When multiple failures are discovered within the same test
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.from_uri('{openapi3_schema_url}')

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize()
@settings(derandomize=True)
@seed(1)
def test_(case):
    case.call_and_validate()""",
    )
    # Then all of them should be displayed
    result = testdir.runpytest()
    result.assert_outcomes(passed=1, failed=1)
    stdout = result.stdout.str()
    assert "[500] Internal Server Error" in stdout
    # And internal frames should not be displayed
    assert "def run_subtest" not in stdout


@pytest.mark.operations("multiple_failures")
def test_multiple_failures_non_check(testdir, openapi3_schema_url):
    # When multiple failures are discovered within the same test
    # And there are non-check exceptions
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.from_uri('{openapi3_schema_url}')

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize()
@settings(derandomize=True)
@seed(1)
def test_(case):
    if case.query["id"] < 0:
        assert 1 == 2
    case.call_and_validate()""",
    )
    # Then all of them should be displayed
    result = testdir.runpytest()
    result.assert_outcomes(passed=1, failed=1)
    stdout = result.stdout.str()
    assert "[500] Internal Server Error" in stdout
    assert "assert 1 == 2" in stdout
    # And internal frames should not be displayed
    assert "def run_subtest" not in stdout
    assert "def collecting_wrapper" not in stdout
    assert stdout.count("test_multiple_failures_non_check.py:37") == 1


@pytest.mark.operations("flaky")
def test_flaky(testdir, openapi3_schema_url):
    # When failure is flaky
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.from_uri('{openapi3_schema_url}')

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize()
def test_(case):
    case.call_and_validate()""",
    )
    # Then it should be properly displayed
    result = testdir.runpytest()
    result.assert_outcomes(passed=1, failed=1)
    stdout = result.stdout.str()
    assert "[500] Internal Server Error" in stdout
    # And internal frames should not be displayed
    assert "def run_subtest" not in stdout
    assert "def collecting_wrapper" not in stdout
    assert "def __flaky" not in stdout


@pytest.mark.operations("failure")
@pytest.mark.parametrize("value", (True, False))
def test_output_sanitization(testdir, openapi3_schema_url, openapi3_base_url, value):
    auth = "secret-auth"
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.from_uri('{openapi3_schema_url}')

lazy_schema = schemathesis.from_pytest_fixture("api_schema", sanitize_output={value})

@lazy_schema.parametrize()
def test_(case):
    case.call_and_validate(headers={{'Authorization': '{auth}'}})""",
    )
    result = testdir.runpytest()
    # We should skip checking for a server error
    result.assert_outcomes(passed=1, failed=1)
    if value:
        expected = rf"E           curl -X GET -H 'Authorization: [Filtered]' {openapi3_base_url}/failure"
    else:
        expected = rf"E           curl -X GET -H 'Authorization: {auth}' {openapi3_base_url}/failure"
    assert expected in result.stdout.lines


@pytest.mark.operations("success")
def test_rate_limit(testdir, openapi3_schema_url):
    if IS_PYRATE_LIMITER_ABOVE_3:
        assertion = """
    assert limiter.bucket_factory.bucket.rates[0].limit == 1
    assert limiter.bucket_factory.bucket.rates[0].interval == 1000
"""
    else:
        assertion = """
    rate = limiter._rates[0]
    assert rate.interval == 1
    assert rate.limit == 1
        """
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.from_uri('{openapi3_schema_url}')

lazy_schema = schemathesis.from_pytest_fixture("api_schema", rate_limit="1/s")

@lazy_schema.parametrize()
def test_(case):
    limiter = case.operation.schema.rate_limiter
    {assertion}
""",
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


@pytest.mark.operations("path_variable", "custom_format")
def test_override(testdir, openapi3_base_url, openapi3_schema_url):
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.from_uri('{openapi3_schema_url}')

lazy_schema = schemathesis.from_pytest_fixture("api_schema")

@lazy_schema.parametrize(endpoint=["path_variable", "custom_format"])
@lazy_schema.override(path_parameters={{"key": "foo"}}, query={{"id": "bar"}})
def test(case):
    if "key" in case.operation.path_parameters:
        assert case.path_parameters["key"] == "foo"
        assert "id" not in (case.query or {{}}), "`id` is present"
    if "id" in case.operation.query:
        assert case.query["id"] == "bar"
        assert "key" not in (case.path_parameters or {{}}), "`key` is present"
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
