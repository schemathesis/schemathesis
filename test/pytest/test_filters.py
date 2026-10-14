from schemathesis.generation.modes import GenerationMode


def test_loader_filter(testdir):
    testdir.make_test(
        """
@schema.include(method="POST", path_regex="/foo").parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.operation.path == "/foo"
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
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 2$"])


def test_error_on_no_matches(testdir):
    # When test filters don't match any operation
    testdir.make_test(
        """
@schema.include(operation_id=["does-not-exist"]).parametrize()
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
