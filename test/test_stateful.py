import pytest


@pytest.fixture(params=["stateful_v2.yaml", "stateful_v3.yaml"])
def testdir(request, testdir):
    def make_stateful_test(*args, **kwargs):
        kwargs["schema_name"] = request.param
        testdir.make_test(*args, **kwargs)

    testdir.make_stateful_test = make_stateful_test

    def assert_stateful(stateful):
        passed = 1
        tests_num = 1
        if stateful:
            passed = 6
            tests_num = 6

        result = testdir.runpytest("-v", "-s")
        result.assert_outcomes(passed=passed)
        result.stdout.re_match_lines([rf"Hypothesis calls: {tests_num}"])

    testdir.assert_stateful = assert_stateful
    testdir.param = request.param  # `request.param` is not available in test for some reason

    return testdir


@pytest.mark.parametrize("stateful", (False, True))
def test_no_dependencies(testdir, stateful):
    """Test schema without dependent endpoints."""
    testdir.make_test(
        f"""
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.endpoint.schema.stateful == {stateful}
""",
        stateful=stateful,
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([rf"Hypothesis calls: 1$"])


@pytest.mark.parametrize("stateful", (False, True))
def test_dependencies(testdir, stateful):
    """Test schema with dependent endpoints."""
    testdir.make_stateful_test(
        f"""
@schema.parametrize(method="PATCH")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.endpoint.schema.stateful == {stateful}
    if case.endpoint.method == "PATCH":
        assert case.endpoint.dependency_count
""",
        stateful=stateful,
    )
    testdir.assert_stateful(stateful)


@pytest.mark.parametrize("schema_stateful", (False, True))
@pytest.mark.parametrize("stateful", (False, True))
def test_parametrize(testdir, stateful, schema_stateful):
    """Test schema with dependent endpoints, overwrite `stateful` parameter in schema."""
    testdir.make_stateful_test(
        f"""
@schema.parametrize(method="PATCH", stateful={stateful})
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.endpoint.schema.stateful == {stateful}
    if case.endpoint.method == "PATCH":
        assert case.endpoint.dependency_count
""",
        stateful=schema_stateful,
    )
    testdir.assert_stateful(stateful)
