from .utils import integer


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
