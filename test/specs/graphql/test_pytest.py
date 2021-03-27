def test_basic_pytest_graphql(testdir, graphql_url):
    testdir.make_test(
        f"""
schema = schemathesis.graphql.from_url('{graphql_url}')

@schema.parametrize()
@settings(max_examples=10)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/graphql"
    assert case.operation.verbose_name in case.body
    response = case.call()
    assert response.status_code == 200
    case.validate_response(response)
    case.call_and_validate()
""",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"test_basic_pytest_graphql.py::test_\[getBooks]\[P\] PASSED",
            r"test_basic_pytest_graphql.py::test_\[getAuthors]\[P\] PASSED",
            r"Hypothesis calls: 20",
        ]
    )
