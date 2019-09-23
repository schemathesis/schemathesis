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
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])
