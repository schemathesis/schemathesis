import datetime

import yaml
from hypothesis import given

import schemathesis

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
@schema.parametrize(method="POST")
@settings(max_examples=3)
def test_(case):
    assert_int(case.body)
        """,
        paths={
            "/users": {
                "post": {"parameters": [{"name": "id", "in": "body", "required": True, "schema": {"type": "integer"}}]}
            }
        },
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


def test_form_data(testdir):
    # When parameter is specified for "form_data"
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    assert_str(case.form_data["status"])
        """,
        **as_param({"name": "status", "in": "formData", "required": True, "type": "string"}),
    )
    # Then the generated test case should contain it in its `form_data` attribute
    testdir.run_and_assert(passed=1)


def test_unknown_data(testdir):
    # When parameter is specified for unknown "in"
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
        """,
        **as_param({"name": "status", "in": "unknown", "required": True, "type": "string"}),
    )
    # Then the generated test ignores this parameter
    testdir.run_and_assert(passed=1)


def test_date_deserializing(testdir):
    # When dates in schema are written without quotes (achieved by dumping the schema with date instances)
    schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/teapot": {
                "get": {
                    "summary": "Test",
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {
                                "allOf": [
                                    # For sake of example to check allOf logic
                                    {"type": "string", "example": datetime.date(2020, 1, 1)},
                                    {"type": "string", "example": datetime.date(2020, 1, 1)},
                                ]
                            },
                        }
                    ],
                }
            }
        },
    }

    schema_path = testdir.makefile(".yaml", schema=yaml.dump(schema))
    # Then yaml loader should ignore it
    # And data generation should work without errors
    schema = schemathesis.from_path(str(schema_path))

    @given(case=schema["/teapot"]["GET"].as_strategy())
    def test(case):
        assert isinstance(case.query["key"], str)

    test()
