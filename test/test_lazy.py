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
    assert case.path == "/v1/users"
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
    assert case.path == "/v1/users"
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
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize(endpoint="/first")
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/first"

@lazy_schema.parametrize(method="POST")
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "POST"
""",
        paths={"/first": {"post": {}, "get": {}}, "/second": {"post": {}, "get": {}}},
    )
    result = testdir.runpytest("-v")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"test_with_parametrize_filters.py::test_a PASSED",
            r"test_with_parametrize_filters.py::test_b PASSED",
            r".*2 passed",
        ]
    )
    result.stdout.re_match_lines([r"Hypothesis calls: 4$"])


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
    assert case.path == "/v1/second"

@lazy_schema.parametrize()
def test_c(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        paths={"/first": {"post": {}, "get": {}}, "/second": {"post": {}, "get": {}}},
        method="POST",
        endpoint="/first",
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
    # test_a: 3 = 3 GET to /first, /second, /users
    # test_b: 2 = 1 GET + 1 POST to /second
    # test_c: 1 = 1 POST to /first
    result.stdout.re_match_lines([r"Hypothesis calls: 6$"])


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
    assert case.path == "/v1/pets"
    assert case.method == "POST"
""",
        paths={"/pets": {"post": {}}},
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
    assert case.path == "/v1/second"
""",
        paths={"/first": {"post": {}, "get": {}}, "/second": {"post": {}, "get": {}}},
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
    assert case.path == "/v1/second"
    assert case.method == "GET"

@lazy_schema.parametrize(endpoint=None, method="GET")
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "GET"

@lazy_schema.parametrize(endpoint="/second", method=None)
def test_c(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/second"

@lazy_schema.parametrize()
def test_d(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/first"
    assert case.method == "POST"

""",
        paths={"/first": {"post": {}, "get": {}}, "/second": {"post": {}, "get": {}}},
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
