import pytest
from hypothesis import given, settings

import schemathesis

from .utils import integer


def test_parametrization(testdir):
    # When `schema.parametrize` is specified on a test function
    testdir.make_test(
        """
@schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method == "GET"
"""
    )
    # And schema doesn't contain any parameters
    # And schema contains only 1 endpoint
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)
    # Then test name should contain method:endpoint
    # And there should be only 1 hypothesis call
    result.stdout.re_match_lines([r"test_parametrization.py::test_\[GET:/v1/users\] PASSED", r"Hypothesis calls: 1"])


def test_pytest_parametrize(testdir):
    # When `pytest.mark.parametrize` is applied
    testdir.make_test(
        """
@pytest.mark.parametrize("param", ("A", "B"))
@schema.parametrize()
def test_(request, param, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
    )
    # And there are multiple method/endpoint combinations
    result = testdir.runpytest("-v", "-s")
    # Then the total number of tests should be Method/Endpoint combos x parameters in `parametrize`
    # I.e. regular pytest parametrization logic should be applied
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines(
        [
            r"test_pytest_parametrize.py::test_\[GET:/v1/users\]\[A\] PASSED",
            r"test_pytest_parametrize.py::test_\[GET:/v1/users\]\[B\] PASSED",
            r"Hypothesis calls: 4",
        ]
    )


def test_method(testdir):
    # When tests are written as methods
    testdir.make_test(
        """
class TestAPI:
    @schema.parametrize()
    def test_(self, request, case):
        request.config.HYPOTHESIS_CASES += 1
        assert case.full_path == "/v1/users"
        assert case.method in ("GET", "POST")
""",
        paths={
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
    )
    # Then they should work as regular tests
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"test_method.py::TestAPI::test_\[GET:/v1/users\] PASSED",
            r"test_method.py::TestAPI::test_\[POST:/v1/users\] PASSED",
            r"Hypothesis calls: 2",
        ]
    )


def test_max_examples(testdir):
    # When `max_examples` is specified
    parameters = {"parameters": [integer(name="id", required=True)], "responses": {"200": {"description": "OK"}}}
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        paths={"/users": {"get": parameters, "post": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    # Then total number of Hypothesis calls should be `max_examples` per pytest test
    result.stdout.re_match_lines([r"Hypothesis calls: 10$"])


def test_direct_schema(testdir):
    # When body has schema specified directly, not via $ref
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method == "POST"
    assert_list(case.body)
    assert_str(case.body[0])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    # Then it should be correctly used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_specified_example_body(testdir):
    # When the given body parameter contains an example
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize(method="POST")
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.body == {"name": "John"}
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "#/definitions/ObjectRef"},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={
            "ObjectRef": {
                "required": ["name"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}},
                "example": {"name": "John"},
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    # Then this example should be used in tests
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


@pytest.mark.parametrize(
    "schema",
    (
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/query": {
                    "get": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string", "example": "test"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        {
            "swagger": "2.0",
            "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
            "host": "api.example.com",
            "basePath": "/",
            "schemes": ["https"],
            "paths": {
                "/query": {
                    "get": {
                        "parameters": [
                            {"name": "id", "in": "query", "required": True, "type": "string", "x-example": "test"}
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    ),
)
def test_specified_example_query(testdir, schema):
    # When the given query parameter contains an example
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.query == {"id": "test"}
""",
        schema=schema,
    )

    result = testdir.runpytest("-v", "-s")
    # Then this example should be used in tests
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_specified_example_parameter_override(testdir):
    # When the given parameter contains an example
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.query == {"id": "test"}
""",
        schema={
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/query": {
                    "get": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "query",
                                "required": True,
                                "example": "test",
                                "schema": {"type": "string", "example": "NOT test"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )

    result = testdir.runpytest("-v", "-s")
    # Then this example should be used in tests
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_specified_example_body_media_type_override(testdir):
    # When the given requestBody parameter contains an example specified in Media Type Object (not in Schema Object)
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.body == {"name": "John"}
""",
        schema={
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/body": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"],
                                        "example": {"name": "NOT John"},
                                    },
                                    "example": {"name": "John"},
                                }
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )

    result = testdir.runpytest("-v", "-s")
    # Then this example should be used in tests, not the example from the schema
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_multiple_examples_different_locations(testdir):
    # When there are examples for different locations (e.g. body and query)
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.body == {"name": "John"}
    assert case.query == {"age": 35}
""",
        schema={
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/body": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"],
                                        "example": {"name": "NOT John"},
                                    },
                                    "example": {"name": "John"},
                                }
                            }
                        },
                        "parameters": [{"in": "query", "name": "age", "schema": {"type": "integer"}, "example": 35}],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )

    result = testdir.runpytest("-v", "-s")
    # Then these examples should be used in tests as a part of a single request, i.e combined
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_multiple_examples_same_location(testdir):
    # When there are multiple examples in parameters under the same place
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize(method="POST")
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.formatted_path == "/users/1/2"
""",
        schema_name="simple_openapi.yaml",
        paths={
            "/users/{a}/{b}": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"type": "integer", "example": 42},  # This example should be overridden
                            "in": "path",
                            "name": "a",
                            "required": True,
                            "example": 1,
                        },
                        {
                            "schema": {"type": "integer", "example": 43},  # and this one too
                            "in": "path",
                            "name": "b",
                            "required": True,
                            "example": 2,
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    # Then these examples should be used combined in tests
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_deselecting(testdir):
    # When pytest selecting is applied via "-k" option
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1

@schema.parametrize(endpoint="pets")
@settings(max_examples=1)
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    """,
        paths={"/pets": {"post": {"responses": {"200": {"description": "OK"}}}}},
    )
    result = testdir.runpytest("-v", "-s", "-k", "pets")
    # Then only relevant tests should be selected for running
    result.assert_outcomes(passed=2)
    # "/users" endpoint is excluded in the first test function
    result.stdout.re_match_lines([".* 1 deselected / 2 selected", r".*\[POST:/v1/pets\]", r"Hypothesis calls: 2"])


@pytest.mark.parametrize(
    "schema_name, endpoint",
    (
        ("simple_swagger.yaml", {"/users": {"x-handler": "foo"}}),
        ("simple_openapi.yaml", {"/users": {"x-handler": "foo", "description": "Text"}}),
    ),
)
def test_custom_properties(testdir, schema_name, endpoint):
    # When custom properties are present in endpoint definitions (e.g. vendor extensions or some other allowed fields)
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    """,
        schema_name=schema_name,
        paths=endpoint,
    )
    result = testdir.runpytest("-s")
    # Then it should be correctly processed
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_invalid_schema(testdir):
    # When the given schema is not valid
    testdir.makepyfile(
        """
import schemathesis

schema = schemathesis.from_dict({"swagger": "2.0", "paths": 1}, validate_schema=False)

@schema.parametrize()
def test_(request, case):
    pass
""",
        validate_schema=False,
    )
    result = testdir.runpytest()
    # Then collection phase should fail with error
    result.assert_outcomes(error=1)
    result.stdout.re_match_lines([r".*Error during collection$"])


@pytest.mark.parametrize("as_kwarg", (True, False))
def test_invalid_schema_with_parametrize(testdir, as_kwarg):
    # When the given schema is not valid but validation is disabled via validate_schema=False argument
    testdir.make_test(
        """
@schema.parametrize({})
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""".format(
            "" if not as_kwarg else "validate_schema=False"
        ),
        validate_schema=False,
        schema_name="simple_openapi.yaml",
        paths={
            "/users": {
                "get": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                            }
                        }
                    }
                }
            }
        },
    )
    result = testdir.runpytest()
    # Then test should be executed
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_exception_during_test(testdir):
    # When the given schema has logical errors
    testdir.make_test(
        """
@schema.parametrize()
def test_(request, case):
    pass
""",
        paths={
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "type": "string",
                            "in": "query",
                            "name": "key5",
                            "minLength": 10,
                            "maxLength": 6,
                            "required": True,
                        }
                    ]
                }
            }
        },
    )
    result = testdir.runpytest("-v", "-rf")
    # Then the tests should fail with the relevant error message
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([r".*Cannot have max_size=6 < min_size=10", ".*Failed: Cannot"])


def test_invalid_endpoint(testdir):
    # When the given schema is invalid
    # And schema validation is disabled
    testdir.make_test(
        """
@schema.parametrize()
def test_(request, case):
    pass
""",
        paths={
            "/valid": {"get": {"parameters": [{"type": "integer", "name": "id", "in": "query", "required": True}]}},
            "/invalid": {"get": {"parameters": [{"type": "int", "name": "id", "in": "query", "required": True}]}},
        },
        validate_schema=False,
    )
    result = testdir.runpytest("-v", "-rf")
    # Then the tests should fail with the relevant error message
    result.assert_outcomes(failed=1, passed=2)
    result.stdout.re_match_lines([r".*test_invalid_endpoint.py::test_\[GET:/v1/invalid\] FAILED"])


def test_no_base_path(testdir):
    # When the given schema has no "basePath"
    testdir.make_test(
        """
del raw_schema["basePath"]

@schema.parametrize()
def test_(request, case):
    pass
"""
    )
    result = testdir.runpytest("-v")
    # Then the base path is "/"
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r".*\[GET:/users\]"])


def test_base_url_from_uri(testdir, openapi_3_app, openapi3_base_url, openapi3_schema_url):
    # When the schema is created out of URI
    testdir.make_test(
        f"""
schema = schemathesis.from_uri("{openapi3_schema_url}")

@schema.parametrize()
def test_(request, case):
    assert case._get_base_url(None) == "{openapi3_base_url}"
    case.call()
"""
    )
    result = testdir.runpytest("-v")
    # Then base URL can be discovered automatically
    # And can be omitted in `call`
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines([r".*\[GET:/api/failure\]", r".*\[GET:/api/success\]"])
    assert len(openapi_3_app["incoming_requests"]) == 2


def test_empty_content():
    # When the "content" value is empty in "requestBody"
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {"/body": {"post": {"requestBody": {"content": {}}, "responses": {"200": {"description": "OK"}}}}},
    }
    schema = schemathesis.from_dict(raw_schema)
    # Then the body processing should be no-op
    endpoint = schema.endpoints["/body"]["POST"]
    assert endpoint.body is None


@pytest.mark.hypothesis_nested
def test_loose_multipart_definition():
    # When the schema of "multipart/form-data" content does not define "object" type
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/body": {
                "post": {
                    "requestBody": {
                        "content": {"multipart/form-data": {"schema": {"properties": {"foo": {"type": "string"}}}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.from_dict(raw_schema)
    # Then non-object data should be excluded during generation

    @given(case=schema.endpoints["/body"]["POST"].as_strategy())
    @settings(max_examples=5)
    def test(case):
        assert isinstance(case.form_data, dict)

    # And the resulting values should be valid
    test()


def test_exceptions_on_collect(testdir):
    # When collected item raises an exception during `hasattr` in `is_schemathesis_test`
    testdir.make_test(
        """
@schema.parametrize()
def test_(request, case):
    pass
"""
    )
    testdir.makepyfile(
        test_b="""
    class NotInitialized:
        def __getattr__(self, item):
            raise RuntimeError

    app = NotInitialized()
    """
    )
    result = testdir.runpytest("-v")
    # Then it should not be propagated & collection should be continued
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r".*\[GET:/v1/users\]"])
