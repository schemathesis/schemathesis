import pytest

from .utils import as_param, integer, string


def test_parametrization(testdir):
    # When `schema.parametrize` is specified on a test function
    testdir.make_test(
        """
@schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
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
    assert case.path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        paths={"/users": {"get": {}, "post": {}}},
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


def test_max_examples(testdir):
    # When `max_examples` is specified
    parameters = {"parameters": [integer(name="id", required=True)]}
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        paths={"/users": {"get": parameters, "post": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    # Then total number of Hypothesis calls should be `max_examples` per pytest test
    result.stdout.re_match_lines([r"Hypothesis calls: 10"])


@pytest.mark.parametrize("endpoint", ("'/foo'", "'/v1/foo'", ["/foo"], "'/.*oo'"))
def test_endpoint_filter(testdir, endpoint):
    # When `endpoint` is specified
    parameters = {"parameters": [integer(name="id", required=True)]}
    testdir.make_test(
        """
@schema.parametrize(filter_endpoint={})
@settings(max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/foo"
    assert case.method == "GET"
""".format(
            endpoint
        ),
        paths={"/foo": {"get": parameters}, "/bar": {"get": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    # Then only tests for this endpoints should be generated
    result.stdout.re_match_lines([r"test_endpoint_filter.py::test_\[GET:/v1/foo\] PASSED"])


@pytest.mark.parametrize("method", ("'get'", "'GET'", ["GET"], ["get"]))
def test_method_filter(testdir, method):
    # When `method` is specified
    parameters = {"parameters": [integer(name="id", required=True)]}
    testdir.make_test(
        """
@schema.parametrize(filter_method={})
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path in ("/v1/foo", "/v1/users")
    assert case.method == "GET"
""".format(
            method
        ),
        paths={"/foo": {"get": parameters}, "/bar": {"post": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    # Then only tests for this method should be generated
    result.stdout.re_match_lines(
        [r"test_method_filter.py::test_\[GET:/v1/foo\] PASSED", r"test_method_filter.py::test_\[GET:/v1/users\] PASSED"]
    )


def test_simple_dereference(testdir):
    # When a given parameter contains a JSON reference
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.query["id"])
""",
        **as_param({"$ref": "#/definitions/SimpleIntRef"}),
        definitions={"SimpleIntRef": integer(name="id", required=True)},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_recursive_dereference(testdir):
    # When a given parameter contains a JSON reference, that reference an object with another reference
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.body["id"])
""",
        **as_param({"schema": {"$ref": "#/definitions/ObjectRef"}, "in": "body", "name": "object", "required": True}),
        definitions={
            "ObjectRef": {
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
            },
            "SimpleIntRef": {"type": "integer"},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_inner_dereference(testdir):
    # When a given parameter contains a JSON reference inside a property of an object
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.body["id"])
""",
        **as_param(
            {
                "schema": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
                },
                "in": "body",
                "name": "object",
                "required": True,
            }
        ),
        definitions={"SimpleIntRef": {"type": "integer"}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_inner_dereference_with_lists(testdir):
    # When a given parameter contains a JSON reference inside a list in `allOf`
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.body["id"]["a"])
    assert_str(case.body["id"]["b"])
""",
        **as_param(
            {
                "schema": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"allOf": [{"$ref": "#/definitions/A"}, {"$ref": "#/definitions/B"}]}},
                },
                "in": "body",
                "name": "object",
                "required": True,
            }
        ),
        definitions={
            "A": {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
            "B": {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_common_parameters(testdir):
    # When common parameter is shared on an endpoint level
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method in ["GET", "POST"]
    assert_int(case.query["common_id"])
    assert_int(case.query["not_common_id"])
""",
        paths={
            "/users": {
                "parameters": [integer(name="common_id", required=True)],
                "get": {"parameters": [integer(name="not_common_id", required=True)]},
                "post": {"parameters": [integer(name="not_common_id", required=True)]},
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    # Then this parameter should be used for all specified methods
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines([r"Hypothesis calls: 2"])


def test_direct_schema(testdir):
    # When body has schema specified directly, not via $ref
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_list(case.body)
    assert_str(case.body[0])
""",
        **as_param(
            {
                "schema": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "in": "body",
                "name": "object",
                "required": True,
            }
        ),
    )
    # Then it should be correctly used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_common_parameters_with_references(testdir):
    # When common parameter that is shared on an endpoint level contains a reference
    # And this parameter is in `body`
    # And the schema is used for multiple test functions
    testdir.make_test(
        """
def impl(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method in ["GET", "POST"]
    assert_int(case.body)
    assert_int(case.query["not_common_id"])

@schema.parametrize()
@settings(max_examples=1)
def test_a(request, case):
    impl(request, case)

@schema.parametrize()
@settings(max_examples=1)
def test_b(request, case):
    impl(request, case)
""",
        paths={
            "/users": {
                "parameters": [
                    {"schema": {"$ref": "#/definitions/SimpleIntRef"}, "in": "body", "name": "id", "required": True}
                ],
                "get": {"parameters": [integer(name="not_common_id", required=True)]},
                "post": {"parameters": [integer(name="not_common_id", required=True)]},
            }
        },
        definitions={"SimpleIntRef": {"type": "integer", "name": "id"}},
    )
    result = testdir.runpytest("-v", "-s")
    # Then this parameter should be used in all generated tests
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 4"])


def test_common_parameters_multiple_tests(testdir):
    # When common parameters are specified on an endpoint level
    # And the same schema is used in multiple tests
    testdir.make_test(
        """
def impl(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method in ["GET", "POST"]
    assert_int(case.query["common_id"])

@schema.parametrize()
@settings(max_examples=1)
def test_a(request, case):
    impl(request, case)

@schema.parametrize()
@settings(max_examples=1)
def test_b(request, case):
    impl(request, case)
""",
        paths={"/users": {"parameters": [integer(name="common_id", required=True)], "post": {}}},
    )
    result = testdir.runpytest("-v", "-s")
    # Then this parameter should be used in all test functions
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 4"])
    # NOTE: current implementation requires a deepcopy of the whole schema


def test_specified_example(testdir):
    # When the given parameter contains an example
    testdir.make_test(
        """
from hypothesis import Phase

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.explicit])
def test(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.body == {"name": "John"}
""",
        **as_param({"schema": {"$ref": "#/definitions/ObjectRef"}, "in": "body", "name": "object", "required": True}),
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
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_required_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=20)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert "id" in case.body
""",
        **as_param(
            {
                "in": "body",
                "name": "object",
                "required": True,
                "schema": {"type": "object", "required": ["id"], "properties": {"id": integer(name="id")}},
            }
        ),
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 20"])


def test_not_required_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
""",
        **as_param({"in": "query", "name": "key", "required": False, "type": "string"}),
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_without_required(testdir):
    # When "required" field is not present in the parameter
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    assert case.path == "/v1/users"
    assert case.method == "GET"
    if not case.query:
        request.config.HYPOTHESIS_CASES += 1
""",
        **as_param({"in": "query", "name": "key", "type": "string"}),
    )
    # then the parameter is optional
    # NOTE. could be flaky
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_invalid_schema(testdir):
    # When the given schema is not valid
    testdir.makepyfile(
        """
from schemathesis import Parametrizer

schema = Parametrizer({"swagger": "2.0", "paths": 1})

@schema.parametrize()
def test_(request, case):
    pass
"""
    )
    result = testdir.runpytest()
    # Then collection phase should fail with error
    result.assert_outcomes(error=1)
    result.stdout.re_match_lines([r".*Error during collection$"])


def test_exception_during_test(testdir):
    # When the given schema has logical errors
    testdir.make_test(
        """
@schema.parametrize()
def test_(request, case):
    pass
""",
        paths={"/users": {"get": {"parameters": [string(name="key5", minLength=10, maxLength=6, required=True)]}}},
    )
    result = testdir.runpytest("-v", "-rf")
    # Then the tests should fail with the relevant error message
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([r".*Cannot have max_size=6 < min_size=10", ".*Failed: Cannot"])


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
