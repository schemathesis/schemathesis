import pytest

from .utils import integer


@pytest.mark.parametrize("filter", ["method='GET'", "method='get'", "method_regex='GET'", "method_regex='get'"])
def test_method_filter(testdir, filter):
    # When `method` is specified
    parameters = {"parameters": [integer(name="id", required=True)], "responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        f"""
@schema.include({filter}).parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.full_path in ("/v1/foo", "/v1/users")
    assert case.method == "GET"
""",
        paths={"/foo": {"get": parameters}, "/bar": {"post": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    # Then only tests for this method should be generated
    result.stdout.re_match_lines(
        [
            r"test_method_filter.py::test_[GET /v1/foo] PASSED",
            r"test_method_filter.py::test_[GET /v1/users] PASSED",
        ]
    )


def test_tag_filter(testdir):
    # When `tag` is specified
    parameters = {"parameters": [integer(name="id", required=True)], "responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.include(tag="bar").parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.full_path == "/v1/bar"
    assert case.method == "GET"
""",
        paths={
            "/foo": {"get": {**parameters, "tags": ["foo", "baz"]}},
            "/bar": {"get": {**parameters, "tags": ["bar", "baz"]}},
        },
        tags=[{"name": "foo"}, {"name": "bar"}, {"name": "baz"}],
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    # Then only tests for this tag should be generated
    result.stdout.re_match_lines([r"test_tag_filter.py::test_[GET /v1/bar] PASSED"])


def test_loader_filter(testdir):
    testdir.make_test(
        """
@schema.include(method="POST", path_regex="/foo").parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.full_path == "/v1/foo"
    assert case.method == "POST"
""",
        paths={
            "/foo": {
                "post": {"parameters": [], "responses": {"200": {"description": "OK"}}},
                "get": {"parameters": [], "responses": {"200": {"description": "OK"}}},
            },
            "/bar": {
                "post": {"parameters": [], "responses": {"200": {"description": "OK"}}},
                "get": {"parameters": [], "responses": {"200": {"description": "OK"}}},
            },
        },
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_operation_id_filter(testdir):
    parameters = {"responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.include(operation_id="bar_get").parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.full_path == "/v1/bar"
    assert case.method == "GET"
""",
        paths={
            "/foo": {"get": {**parameters, "operationId": "foo_get"}},
            "/bar": {"get": {**parameters, "operationId": "bar_get"}},
        },
        schema_name="simple_openapi.yaml",
    )

    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)

    result.stdout.re_match_lines([r"test_operation_id_filter.py::test_[GET /v1/bar] PASSED"])


def test_operation_id_list_filter(testdir):
    parameters = {"responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.include(operation_id=["foo_get", "foo_post"]).parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.full_path == "/v1/foo"
""",
        paths={
            "/foo": {
                "get": {**parameters, "operationId": "foo_get"},
                "post": {**parameters, "operationId": "foo_post"},
            },
            "/bar": {"get": {**parameters, "operationId": "bar_get"}},
        },
        schema_name="simple_openapi.yaml",
    )

    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)

    result.stdout.re_match_lines([r"test_operation_id_list_filter.py::test_[GET /v1/foo] PASSED"])
    result.stdout.re_match_lines([r"test_operation_id_list_filter.py::test_[POST /v1/foo] PASSED"])


def test_error_on_no_matches(testdir):
    # When test filters don't match any operation
    testdir.make_test(
        """
@schema.include(operation_id=["does-not-exist"]).parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
    )
    result = testdir.runpytest("-v")
    # Then it should be an error
    result.assert_outcomes(errors=1)
    result.stdout.re_match_lines(
        [
            r"E *Failed: Test function test_error_on_no_matches.py::test_ does not "
            r"match any API operations and therefore has no effect"
        ]
    )
