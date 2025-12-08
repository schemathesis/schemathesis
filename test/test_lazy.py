import re

import pytest
from hypothesis import settings

from schemathesis.generation.modes import GenerationMode


@pytest.fixture
def reload_profile():
    yield
    settings.load_profile("default")


def test_default(testdir):
    # When LazySchema is used
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(case):
    pass
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
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

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


def test_invalid_operation(testdir, hypothesis_max_examples):
    # When the given schema is invalid
    # And schema validation is disabled
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

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
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v", "-rf")
    # Then one test should be marked as failed (passed - /users, failed /)
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines(
        [
            r"test_invalid_operation.py::test_\[GET /valid\] \(label='GET /valid'\) SUBPASS +\[ 25%\]",
            r"test_invalid_operation.py::test_\[GET /invalid\] \(label='GET /invalid'\) SUBFAIL +\[ 50%\]",
            r"test_invalid_operation.py::test_\[GET /users\] \(label='GET /users'\) SUBPASS +\[ 75%\]",
            r"test_invalid_operation.py::test_ PASSED +\[100%\]",
        ]
    )
    # 100 for /valid, 1 for /users
    hypothesis_calls = (hypothesis_max_examples or 100) + 4
    result.stdout.re_match_lines([rf"Hypothesis calls: {hypothesis_calls}$"])


def test_with_fixtures(testdir):
    # When the test uses custom arguments for pytest fixtures
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@pytest.fixture
def another():
    return 1

@lazy_schema.parametrize()
def test_(request, case, another):
    request.config.HYPOTHESIS_CASES += 1
    assert another == 1
"""
    )
    result = testdir.runpytest("-v")
    # Then the generated test should use these fixtures
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"test_with_fixtures.py::test_ PASSED", r".*1 passed"])
    result.stdout.re_match_lines([r"Hypothesis calls: 8$"])


def test_with_parametrize_filters(testdir):
    # When the test uses method / endpoint / tag / operation-id filter
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.include(path_regex="/first").parametrize()
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.path == "/first"

@lazy_schema.include(method="POST").parametrize()
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "POST"

@lazy_schema.include(tag="foo").parametrize()
def test_c(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.path == "/second"
    assert case.method == "GET"

@lazy_schema.include(operation_id="updateThird").parametrize()
def test_d(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.path == "/third"
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
        generation_modes=[GenerationMode.POSITIVE],
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
    result.stdout.re_match_lines([r"Hypothesis calls: 12$"])


def test_with_schema_filters(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema").include(path_regex="/pets", method="POST")

@lazy_schema.parametrize()
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.path == "/pets"
    assert case.method == "POST"
""",
        paths={"/pets": {"post": {"responses": {"200": {"description": "OK"}}}}},
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"test_with_schema_filters.py::test_a PASSED"])
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_invalid_fixture(testdir):
    # When the test uses a schema fixture that doesn't return a BaseSchema subtype
    testdir.make_test(
        """
@pytest.fixture
def bad_schema():
    return 1

lazy_schema = schemathesis.pytest.from_fixture("bad_schema")

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


@pytest.mark.parametrize("given", ["data=st.data()", "st.data()"])
def test_schema_given(testdir, given):
    # When the schema is defined via a pytest fixture
    # And `schema.given` is used
    testdir.make_test(
        f"""
from hypothesis.strategies._internal.core import DataObject

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")
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


def test_schema_given_before_parametrize(testdir):
    # See GH-3320
    # When `schema.given` is applied before `schema.parametrize`
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")


@lazy_schema.given(user_id=st.sampled_from([1, 2, 3]))
@lazy_schema.parametrize()
def test_user_id(case, user_id):
    pass
        """,
        paths={
            "/users/{user_id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "type": "integer",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_invalid_given_usage(testdir):
    # When `schema.given` is used incorrectly (e.g. called without arguments)
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

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


@pytest.mark.parametrize("settings", ["", "@settings(deadline=None)"])
def test_parametrized_fixture(testdir, openapi3_base_url, settings):
    # When the used pytest fixture is parametrized via `params`
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")

@pytest.fixture(params=["a", "b"])
def parametrized_lazy_schema(request):
    return schema

lazy_schema = schemathesis.pytest.from_fixture("parametrized_lazy_schema")

@lazy_schema.parametrize()
{settings}
def test_(case):
    case.call()
""",
    )
    result = testdir.runpytest("-v")
    # Then tests should be parametrized as usual
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"test_parametrized_fixture.py::test_\[a\]\[GET /users\] \(label='GET /users'\) SUBPASS +\[ 33%\]",
            r"test_parametrized_fixture.py::test_\[b\]\[GET /users\] \(label='GET /users'\) SUBPASS +\[ 75%\]",
        ]
    )


def test_generation_modes(testdir):
    # When data generation method config is specified on the schema which is wrapped by a lazy one
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    schema = schemathesis.openapi.from_dict(raw_schema)
    schema.config.generation.update(modes=list(GenerationMode))
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

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
    # And it should be the same test in the end
    # We do not assert the outcome here, because it is not reported.
    result.stdout.re_match_lines([r"test_generation_modes.py::test_\[GET /users\] \(label='GET /users'\) SUBPASS"])


def test_error_on_no_matches(testdir):
    # When test filters don't match any operation
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    return schemathesis.openapi.from_dict(raw_schema)

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.include(operation_id=["does-not-exist"]).parametrize()
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
    [
        """@schema.parametrize()
@pytest.mark.acceptance""",
        """@pytest.mark.acceptance
@schema.parametrize()""",
    ],
)
def test_marks_transfer(testdir, decorators):
    # See GH-1378
    # When a pytest mark decorator is applied
    testdir.make_test(
        f"""
@pytest.fixture
def web_app():
    1 / 0

schema = schemathesis.pytest.from_fixture("web_app")

{decorators}
def test_schema(case):
    1 / 0
    """
    )
    result = testdir.runpytest("-m", "not acceptance")
    # Then deselecting by a mark should work
    result.assert_outcomes()


def test_skip_negative_without_parameters(testdir):
    # See GH-1463
    # When an endpoint has no parameters to negate
    testdir.make_test(
        """
@pytest.fixture()
def api_schema():
    schema = schemathesis.openapi.from_dict(raw_schema)
    schema.config.generation.update(modes=[schemathesis.GenerationMode.NEGATIVE])
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
def test_(case):
    pass
""",
    )
    # Then it should be skipped
    result = testdir.runpytest("-v", "-rs")
    result.stdout.re_match_lines([r".*Impossible to generate negative test cases.*"])


def test_trimmed_output(testdir):
    # When `from_fixture` is used
    # And tests are failing
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

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
    schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

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


@pytest.mark.operations("flaky")
def test_flaky(testdir, openapi3_schema_url):
    # When failure is flaky
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_url('{openapi3_schema_url}')

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

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
@pytest.mark.parametrize("value", [True, False])
def test_output_sanitization(testdir, openapi3_schema_url, openapi3_base_url, value):
    auth = "secret-auth"
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
    schema.config.output.sanitization.enabled = {value}
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
def test_(case):
    case.call_and_validate(headers={{'Authorization': '{auth}'}})""",
    )
    result = testdir.runpytest()
    # We should skip checking for a server error
    result.assert_outcomes(passed=1, failed=1)
    if value:
        expected = rf"curl -X GET -H 'Authorization: [Filtered]' {openapi3_base_url}/failure"
    else:
        expected = rf"curl -X GET -H 'Authorization: {auth}' {openapi3_base_url}/failure"
    assert expected in result.stdout.str()


@pytest.mark.operations("success")
def test_rate_limit(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
    schema.config.update(rate_limit="1/s")
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
def test_(case):
    limiter = case.operation.schema.config.rate_limit
    assert limiter.bucket_factory.bucket.rates[0].limit == 1
    assert limiter.bucket_factory.bucket.rates[0].interval == 1000
""",
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


@pytest.mark.operations("path_variable", "custom_format")
def test_override(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_url('{openapi3_schema_url}')
    schema.config.update(parameters={{"key": "foo", "id": "bar"}})
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.include(path_regex="path_variable|custom_format").parametrize()
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
    result.assert_outcomes(passed=1)


def test_async_fixture(testdir, openapi3_schema_url):
    testdir.make_test(
        f"""
import pytest_asyncio
import schemathesis


@pytest_asyncio.fixture
async def lazy_schema():
    return schemathesis.openapi.from_url('{openapi3_schema_url}')


schema = schemathesis.pytest.from_fixture("lazy_schema")


@schema.parametrize()
async def test_pass(case):
    pass


@schema.parametrize()
async def test_fail(case):
    1 / 0
            """
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=2, failed=2)


def test_phases_from_config(testdir):
    # When test phases are configured in schemathesis.toml
    testdir.makefile(
        ".toml",
        schemathesis="""
[phases.examples]
enabled = false

[phases.coverage]
enabled = false
""",
    )

    # Then phases should be applied when using from_fixture
    testdir.make_test(
        """
import pytest
import schemathesis

@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict({
        "openapi": "3.0.0",
        "paths": {
            "/users": {
                "get": {
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
def test_api(case):
    assert case.meta.phase.name == "fuzzing"
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_hypothesis_settings_from_config(testdir):
    # When hypothesis settings are configured in schemathesis.toml for a specific operation
    testdir.makefile(
        ".toml",
        schemathesis="""
[[operations]]
include-path = "/users"

[operations.generation]
max-examples = 5

[phases.examples]
enabled = false

[phases.coverage]
enabled = false
""",
    )

    # Then operation-specific hypothesis settings should be applied when using from_fixture
    testdir.make_test(
        """
import pytest
import schemathesis

@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict({
        "openapi": "3.0.0",
        "paths": {
            "/users": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "schema": {"type": "integer"}}
                    ],
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

call_count = 0

@lazy_schema.parametrize()
def test_api(case):
    global call_count
    call_count += 1
    print(f"Call count: {call_count}")
"""
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    counts = re.findall(r"Call count: (\d+)", result.stdout.str())
    max_count = int(counts[-1])
    assert max_count <= 10, f"Expected max_examples=5 to limit calls to ~5, but got {max_count}"


def test_lazy_fixture_with_test_class(testdir):
    # When using from_fixture with a test method inside a class
    testdir.make_test(
        """
import pytest
import schemathesis

@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict({
        "openapi": "3.0.0",
        "paths": {
            "/users": {
                "get": {
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

class TestAPI:
    @lazy_schema.parametrize()
    def test_users(self, case):
        assert case.operation.path == "/users"
"""
    )
    # Then it should work without crashes
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_checks_available_with_from_fixture(tmp_path):
    # When using from_fixture, checks should be accessible without AttributeError
    # Run in subprocess to avoid test pre-loading modules
    import subprocess
    import sys

    test_file = tmp_path / "test_isolated.py"
    test_file.write_text("""
import pytest
import schemathesis
from hypothesis import settings, Phase

@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict({
        "openapi": "3.0.0",
        "paths": {
            "/users": {
                "get": {
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_checks_are_loaded(case):
    _ = schemathesis.checks.status_code_conformance
""")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )

    assert result.returncode == 0, f"Test failed:\n{result.stdout}\n{result.stderr}"


def test_operations_disabled_via_config_with_from_fixture(testdir):
    # Given a schemathesis.toml that disables a specific operation
    testdir.makefile(
        ".toml",
        schemathesis="""
[[operations]]
include-name = "POST /users"
enabled = false
""",
    )

    # And a schema with multiple operations
    testdir.make_test(
        """
import pytest
import schemathesis

@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict({
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {"200": {"description": "OK"}}
                },
                "post": {
                    "responses": {"200": {"description": "OK"}}
                }
            },
            "/products": {
                "get": {
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    })

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
def test_api(case):
    pass
"""
    )

    result = testdir.runpytest("-v")

    # Then `POST /users` should be excluded, only `GET /users` and `GET /products` should be tested
    # With pytest-subtests, the main test passes once, with 2 subtests (not 3)
    result.assert_outcomes(passed=1)

    assert "POST /users" not in result.stdout.str()
    assert "GET /users" in result.stdout.str()
    assert "GET /products" in result.stdout.str()


@pytest.mark.usefixtures("reload_profile")
def test_hypothesis_settings_database_from_profile_lazy(testdir):
    # When a hypothesis profile with a custom database is loaded
    # And lazy schema parametrize is used
    testdir.make_test(
        """
import tempfile
from hypothesis import settings as hyp_settings, database

# Create a custom database in a temporary directory
custom_db_path = tempfile.mkdtemp()
custom_db = database.DirectoryBasedExampleDatabase(custom_db_path)

# Register and load the profile (derandomize=False is required when using a database)
hyp_settings.register_profile("custom_db_profile", database=custom_db, derandomize=False)
hyp_settings.load_profile("custom_db_profile")

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
@hyp_settings(max_examples=1)
def test_(case):
    # hypothesis.settings() returns the loaded profile, which has our custom db
    import hypothesis
    current_settings = hypothesis.settings()
    assert current_settings.database is not None, "Database should not be None"
    assert str(current_settings.database.path) == custom_db_path, (
        f"Expected database path {custom_db_path}, got {current_settings.database.path}"
    )
""",
    )
    result = testdir.runpytest("-v", "-s")
    # Then the custom database from the profile should be used
    result.assert_outcomes(passed=1)


@pytest.mark.usefixtures("reload_profile")
def test_hypothesis_settings_decorator_database_lazy(testdir):
    # When @settings decorator with a custom database is applied to a test
    # And lazy schema parametrize is used
    testdir.make_test(
        """
import tempfile
from hypothesis import settings as hyp_settings, database

# Create a custom database in a temporary directory
custom_db_path = tempfile.mkdtemp()
custom_db = database.DirectoryBasedExampleDatabase(custom_db_path)

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
@hyp_settings(max_examples=1, database=custom_db, derandomize=False)
def test_(case):
    import hypothesis
    current_settings = hypothesis.settings()
    assert current_settings.database is not None, "Database should not be None"
    assert str(current_settings.database.path) == custom_db_path, (
        f"Expected database path {custom_db_path}, got {current_settings.database.path}"
    )
""",
    )
    result = testdir.runpytest("-v", "-s")
    # Then the custom database from the decorator should be used
    result.assert_outcomes(passed=1)
