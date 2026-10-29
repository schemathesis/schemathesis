def test_basic_pytest_graphql(ctx, testdir):
    api = ctx.graphql.apps.books()
    testdir.make_test(
        f"""
from schemathesis.generation import GenerationMode

schema = schemathesis.graphql.from_url('{api.schema_url}')
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.parametrize()
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/graphql"
    assert case.operation.definition.field_name in case.body
    response = case.call()
    assert response.status_code == 200
    case.validate_response(response)
    case.call_and_validate()
""",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_basic_pytest_graphql.py::test_\[Query.getBooks] PASSED",
            r"test_basic_pytest_graphql.py::test_\[Query.getAuthors] PASSED",
            r"test_basic_pytest_graphql.py::test_\[Mutation.addBook] PASSED",
            r"test_basic_pytest_graphql.py::test_\[Mutation.addAuthor] PASSED",
            r"Hypothesis calls: 46",
        ]
    )


def test_from_wsgi(testdir):
    testdir.make_test(
        """
from test.apps.catalog.graphql.bookstore import books
from schemathesis.generation import GenerationMode

schema = schemathesis.graphql.from_wsgi("/graphql", app=books().server)
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.parametrize()
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/graphql"
    assert case.operation.definition.field_name in case.body
    response = case.call_and_validate()
    assert response.status_code == 200
""",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_from_wsgi.py::test_\[Query.getBooks] PASSED",
            r"test_from_wsgi.py::test_\[Query.getAuthors] PASSED",
            r"test_from_wsgi.py::test_\[Mutation.addBook] PASSED",
            r"test_from_wsgi.py::test_\[Mutation.addAuthor] PASSED",
            r"Hypothesis calls: 46",
        ]
    )


def test_from_fixture_uses_field_labels(testdir):
    testdir.make_test(
        '''
@pytest.fixture
def api_schema():
    return schemathesis.graphql.from_file("""
type Query {
  getBooks: [String]
  getAuthors: [String]
}
""")

schema = schemathesis.pytest.from_fixture("api_schema")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    assert case.operation.label != "Query.getAuthors"
''',
    )
    result = testdir.runpytest("-v")
    result.stdout.re_match_lines(
        [
            r"test_from_fixture_uses_field_labels.py::test_api\[Query.getBooks\] SUBPASSED",
            r"test_from_fixture_uses_field_labels.py::test_api\[Query.getAuthors\] SUBFAILED",
        ]
    )
