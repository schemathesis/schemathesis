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
    assert case.path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        paths={"/users": {"get": {}, "post": {}}},
    )
    # And there are multiple method/endpoint combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be Method/Endpoint combos x parameters in `pytest_generate_tests`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize_fixture.py::test_\[GET:/v1/users\]\[A\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[GET:/v1/users\]\[B\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[POST:/v1/users\]\[A\] PASSED",
            r"test_pytest_parametrize_fixture.py::test_\[POST:/v1/users\]\[B\] PASSED",
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
        assert case.path == "/v1/users"
        assert case.method in ("GET", "POST")
""",
        paths={"/users": {"get": {}, "post": {}}},
    )
    # And there are multiple method/endpoint combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be Method/Endpoint combos x parameters in `pytest_generate_tests`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[GET:/v1/users\]\[A\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[GET:/v1/users\]\[B\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[POST:/v1/users\]\[A\] PASSED",
            r"test_pytest_parametrize_class_fixture.py::TestAPI::test_\[POST:/v1/users\]\[B\] PASSED",
            r"Hypothesis calls: 4",
        ]
    )
