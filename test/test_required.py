from .utils import as_param, integer


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
