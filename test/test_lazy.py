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


def test_with_filters(testdir):
    # When the test uses method / endpoint filter
    testdir.make_test(
        """
@pytest.fixture
def simple_schema():
    return schema

lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize(endpoint="/pets")
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/pets"
    assert case.method == "POST"

@lazy_schema.parametrize(method="POST")
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/pets"
    assert case.method == "POST"
""",
        paths={"/pets": {"post": {}}},
    )
    result = testdir.runpytest("-v")
    # Then the filters should be applied to the generated tests
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [r"test_with_filters.py::test_a PASSED", r"test_with_filters.py::test_b PASSED", r".*2 passed"]
    )
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


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
