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


def test_deselecting(testdir):
    # When pytest selecting is applied via "-k" option
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_a(request, case):
    request.config.HYPOTHESIS_CASES += 1

@schema.parametrize(filter_endpoint="pets")
@settings(max_examples=1)
def test_b(request, case):
    request.config.HYPOTHESIS_CASES += 1
    """,
        paths={"/pets": {"post": {}}},
    )
    result = testdir.runpytest("-v", "-s", "-k", "pets")
    # Then only relevant tests should be selected for running
    result.assert_outcomes(passed=2)
    # "/users" endpoint is excluded in the first test function
    result.stdout.re_match_lines([".* 1 deselected / 2 selected", r".*\[POST:/v1/pets\]", r"Hypothesis calls: 2"])


def test_invalid_schema(testdir):
    # When the given schema is not valid
    testdir.makepyfile(
        """
import schemathesis

schema = schemathesis.from_dict({"swagger": "2.0", "paths": 1})

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
