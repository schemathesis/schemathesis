from .utils import as_param


def test_headers(testdir):
    # When parameter is specified for "header"
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    assert_str(case.headers["api_key"])
        """,
        **as_param({"name": "api_key", "in": "header", "required": True, "type": "string"}),
    )
    # Then the generated test case should contain it in its `headers` attribute
    testdir.run_and_assert(passed=1)


def test_cookies(testdir):
    # When parameter is specified for "cookie"
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    assert_str(case.cookies["token"])
        """,
        schema_name="simple_openapi.yaml",
        **as_param({"name": "token", "in": "cookie", "required": True, "schema": {"type": "string"}}),
    )
    # Then the generated test case should contain it in its `cookies` attribute
    testdir.run_and_assert(passed=1)


def test_body(testdir):
    # When parameter is specified for "body"
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=3)
def test_(case):
    assert_int(case.body)
        """,
        **as_param({"name": "id", "in": "body", "required": True, "schema": {"type": "integer"}}),
    )
    # Then the generated test case should contain it in its `body` attribute
    testdir.run_and_assert(passed=1)


def test_path(testdir):
    # When parameter is specified for "path"
    testdir.make_test(
        """
@schema.parametrize(endpoint="/users/{user_id}")
@settings(max_examples=3)
def test_(case):
    assert_int(case.path_parameters["user_id"])
        """,
        paths={
            "/users/{user_id}": {
                "get": {"parameters": [{"name": "user_id", "required": True, "in": "path", "type": "integer"}]}
            }
        },
    )
    # Then the generated test case should contain it its `path_parameters` attribute
    testdir.run_and_assert(passed=1)
