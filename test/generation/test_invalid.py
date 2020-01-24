from test.utils import as_param

import pytest


@pytest.mark.parametrize(
    "parameter",
    (
        {"type": "string", "in": "query", "name": "id"},
        {"type": "string", "in": "formData", "name": "id"},
        {"type": "string", "in": "header", "name": "id"},
        {"schema": {"type": "string"}, "name": "id", "in": "body"},
    ),
)
def test_simple_places(testdir, parameter):
    testdir.make_test(
        """
@schema.parametrize(input_types=[InputType.invalid], method="POST")
@settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
def test_(case):
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_requests_call(case)
        """,
        paths={"/users": {"post": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}}},
    )
    testdir.run_and_assert(passed=1)


def test_path_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize(endpoint="{user_id}", input_types=[InputType.invalid])
@settings(max_examples=10, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
def test_(case):
    assert case.path == "/v1/users/{user_id}"
    assert case.method == "GET"
    assert_requests_call(case)
        """,
        paths={
            "/users/{user_id}": {
                "get": {
                    "parameters": [{"name": "user_id", "required": True, "in": "path", "type": "integer"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    testdir.run_and_assert("-s", passed=1)


def test_cookies(testdir):
    testdir.make_test(
        """
@schema.parametrize(input_types=[InputType.invalid])
@settings(max_examples=10, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
def test_(case):
    assert_requests_call(case)
        """,
        schema_name="simple_openapi.yaml",
        **as_param({"name": "token", "in": "cookie", "required": True, "schema": {"type": "string", "minLength": 5}}),
    )
    testdir.run_and_assert(passed=1)
