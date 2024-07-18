import pytest

from .utils import integer


@pytest.mark.parametrize("endpoint", ("'/foo'", "'/v1/foo'", ["/foo"], "'/.*oo'"))
def test_endpoint_filter(testdir, endpoint):
    # When `endpoint` is specified
    parameters = {"parameters": [integer(name="id", required=True)], "responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.parametrize(endpoint={})
@settings(max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/foo"
    assert case.method == "GET"
""".format(endpoint),
        paths={"/foo": {"get": parameters}, "/bar": {"get": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    # Then only tests for these API operations should be generated
    result.stdout.re_match_lines([r"test_endpoint_filter.py::test_[GET /v1/foo] PASSED"])


@pytest.mark.parametrize("method", ("'get'", "'GET'", ["GET"], ["get"]))
def test_method_filter(testdir, method):
    # When `method` is specified
    parameters = {"parameters": [integer(name="id", required=True)], "responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.parametrize(method={})
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path in ("/v1/foo", "/v1/users")
    assert case.method == "GET"
""".format(method),
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
@schema.parametrize(tag="bar")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/bar"
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
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/foo"
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
        method="POST",
        path="/v1/foo",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_override_filter(testdir):
    testdir.make_test(
        """
@schema.parametrize(method=None, endpoint="/v1/users", tag=None)
@settings(max_examples=1)
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method == "GET"

@schema.parametrize(method=None, endpoint=None)
@settings(max_examples=1)
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/foo"
    assert case.method == "POST"
""",
        paths={
            "/foo": {
                "post": {
                    "parameters": [integer(name="id", required=True)],
                    "responses": {"200": {"description": "OK"}},
                    "tags": ["foo"],
                }
            }
        },
        method="POST",
        path="/v1/foo",
        tag="foo",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_operation_id_filter(testdir):
    parameters = {"responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.parametrize(operation_id="bar_get")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/bar"
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
@schema.parametrize(operation_id=["foo_get", "foo_post"])
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/foo"
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
@schema.parametrize(operation_id=["does-not-exist"])
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
