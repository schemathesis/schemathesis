import pytest


def test_default(testdir):
    # When LazySchema is used
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

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
@pytest.fixture
def simple_schema():
    return schema

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@settings(phases=[])
@lazy_schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1

"""
    )
    result = testdir.runpytest("-v", "-s")
    # Then settings should be applied to the test
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"test_with_settings.py::test_ PASSED", r".*1 passed"])
    result.stdout.re_match_lines([r"Hypothesis calls: 0$"])


def test_invalid_endpoint(testdir, hypothesis_max_examples):
    # When the given schema is invalid
    # And schema validation is disabled
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

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
    result.stdout.re_match_lines(
        [
            r"test_invalid_endpoint.py::test_[GET:/v1/valid][P] PASSED                 [ 25%]",
            r"test_invalid_endpoint.py::test_[GET:/v1/invalid][P] FAILED               [ 50%]",
            r"test_invalid_endpoint.py::test_[GET:/v1/users][P] PASSED                 [ 75%]",
            r".*1 passed",
        ]
    )
    # 100 for /valid, 1 for /users
    hypothesis_calls = (hypothesis_max_examples or 100) + 1
    result.stdout.re_match_lines([rf"Hypothesis calls: {hypothesis_calls}$"])


def test_with_fixtures(testdir):
    # When the test uses custom arguments for pytest fixtures
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

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
@pytest.fixture
def simple_schema():
    return schema

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


def test_with_parametrize_filters_override(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize(endpoint=None, method="GET")
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "GET"

@lazy_schema.parametrize(endpoint="/second", method=None)
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/second"

@lazy_schema.parametrize()
def test_c(request, case):
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
        endpoint="/first",
        tag="foo",
    )
    result = testdir.runpytest("-v", "-s")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=3)
    result.stdout.re_match_lines(
        [
            r"test_with_parametrize_filters_override.py::test_a PASSED",
            r"test_with_parametrize_filters_override.py::test_b PASSED",
            r"test_with_parametrize_filters_override.py::test_c PASSED",
            r".*3 passed",
        ]
    )
    # test_a: 2 = 2 GET to /first, /second
    # test_b: 2 = 1 GET + 1 POST to /second
    # test_c: 1 = 1 POST to /first
    result.stdout.re_match_lines([r"Hypothesis calls: 5$"])


def test_with_schema_filters(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

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
@pytest.fixture
def simple_schema():
    return schema

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
        endpoint="/first",
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
@pytest.fixture
def simple_schema():
    return schema

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
def simple_schema():
    return 1

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

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
@pytest.fixture
def simple_schema():
    return schema

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        schema=schema_with_get_payload,
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines([r"E       Failed: Body parameters are defined for GET request."])


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
@pytest.fixture
def simple_schema():
    return schema

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.hooks.register
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
