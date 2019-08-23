import pytest

from .utils import as_param, integer


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
    parameters = {"parameters": [integer(name="id")]}
    testdir.make_test(
        """
@schema.parametrize(max_examples=5)
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


@pytest.mark.parametrize("endpoint", ("'/foo'", "'/v1/foo'", ["/foo"], "'/*oo'"))
def test_endpoint_filter(testdir, endpoint):
    # When `endpoint` is specified
    parameters = {"parameters": [integer(name="id")]}
    testdir.make_test(
        """
@schema.parametrize(filter_endpoint={}, max_examples=1)
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
    parameters = {"parameters": [integer(name="id")]}
    testdir.make_test(
        """
@schema.parametrize(filter_method={}, max_examples=1)
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
@schema.parametrize(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.query["id"])
""",
        **as_param({"$ref": "#/definitions/SimpleIntRef"}),
        definitions={"SimpleIntRef": integer(name="id")},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_recursive_dereference(testdir):
    # When a given parameter contains a JSON reference, that reference an object with another reference"
    testdir.make_test(
        """
@schema.parametrize(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.body["object"]["id"])
""",
        **as_param({"schema": {"$ref": "#/definitions/ObjectRef"}, "in": "body", "name": "object"}),
        definitions={
            "ObjectRef": {
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
            },
            "SimpleIntRef": integer(name="id"),
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1"])


def test_custom_format(testdir):
    # When the given string parameter has a custom format value
    testdir.make_test(
        """
@schema.parametrize(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        **as_param({"name": "parameter", "type": "string", "format": "custom_format", "in": "query"}),
    )
    result = testdir.runpytest("-v", "-rs")
    # Then the relevant test case should be skipped
    result.assert_outcomes(skipped=1)
    # And a proper message is written to the output
    result.stdout.re_match_lines([".* Unsupported string format=custom_format"])
    result.stdout.re_match_lines([r"Hypothesis calls: 0"])


def test_common_parameters(testdir):
    # When common parameter is shared on an endpoint level
    testdir.make_test(
        """
@schema.parametrize(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method in ["GET", "POST"]
    assert_int(case.query["common_id"])
    assert_int(case.query["not_common_id"])
""",
        paths={
            "/users": {
                "parameters": [integer(name="common_id")],
                "get": {"parameters": [integer(name="not_common_id")]},
                "post": {"parameters": [integer(name="not_common_id")]},
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    # Then this parameter should be used for all specified methods
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines([r"Hypothesis calls: 2"])


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
    assert_int(case.body["id"])
    assert_int(case.query["not_common_id"])

@schema.parametrize(max_examples=1)
def test_a(request, case):
    impl(request, case)

@schema.parametrize(max_examples=1)
def test_b(request, case):
    impl(request, case)
""",
        paths={
            "/users": {
                "parameters": [{"schema": {"$ref": "#/definitions/SimpleIntRef"}, "in": "body", "name": "id"}],
                "get": {"parameters": [integer(name="not_common_id")]},
                "post": {"parameters": [integer(name="not_common_id")]},
            }
        },
        definitions={"SimpleIntRef": integer(name="id", **{"in": "body"})},
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

@schema.parametrize(max_examples=1)
def test_a(request, case):
    impl(request, case)

@schema.parametrize(max_examples=1)
def test_b(request, case):
    impl(request, case)
""",
        paths={"/users": {"parameters": [integer(name="common_id")], "post": {}}},
    )
    result = testdir.runpytest("-v", "-s")
    # Then this parameter should be used in all test functions
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 4"])
    # NOTE: current implementation requires a deepcopy of the whole schema


def test_required_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize(max_examples=20)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert "id" in case.body["object"]
""",
        **as_param(
            {
                "in": "body",
                "name": "object",
                "schema": {"type": "object", "required": ["id"], "properties": {"id": integer(name="id")}},
            }
        ),
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 20"])
