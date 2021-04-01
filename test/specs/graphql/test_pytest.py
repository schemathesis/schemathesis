def test_basic_pytest_graphql(testdir, graphql_path, graphql_url):
    testdir.make_test(
        f"""
schema = schemathesis.graphql.from_url('{graphql_url}')

@schema.parametrize()
@settings(max_examples=10)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "{graphql_path}"
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


def test_from_wsgi(testdir, graphql_path):
    testdir.make_test(
        f"""
from test.apps._graphql._flask.app import app

schema = schemathesis.graphql.from_wsgi("{graphql_path}", app=app)

@schema.parametrize()
@settings(max_examples=10)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "{graphql_path}"
    assert case.operation.verbose_name in case.body
    response = case.call_wsgi()
    assert response.status_code == 200
    case.validate_response(response)
""",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"test_from_wsgi.py::test_\[getBooks]\[P\] PASSED",
            r"test_from_wsgi.py::test_\[getAuthors]\[P\] PASSED",
            r"Hypothesis calls: 20",
        ]
    )
