from schemathesis.generation.modes import GenerationMode

from ..utils import integer


def test_common_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method in ["GET", "POST"]
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.query["common_id"])
        assert_int(case.query["not_common_id"])
""",
        paths={
            "/users": {
                "parameters": [integer(name="common_id", required=True)],
                "get": {
                    "parameters": [integer(name="not_common_id", required=True)],
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "parameters": [integer(name="not_common_id", required=True)],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines([r"Hypothesis calls: 8$"])


def test_common_parameters_with_references(testdir):
    # A shared `body` parameter behind a reference, used by several test functions.
    testdir.make_test(
        """
def impl(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method in ["PUT", "POST"]
    if case.method == "POST":
        assert_int(case.body)
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.query["not_common_id"])
        assert_int(case.query["key"])

@schema.include(path_regex="/foo").parametrize()
@settings(max_examples=1)
def test_a(request, case):
    impl(request, case)

@schema.include(path_regex="/foo").parametrize()
@settings(max_examples=1)
def test_b(request, case):
    impl(request, case)
""",
        paths={
            "/foo": {
                "parameters": [
                    {"$ref": "#/parameters/Param"},
                    {"schema": {"$ref": "#/definitions/SimpleIntRef"}, "in": "body", "name": "id", "required": True},
                ],
                "put": {
                    "parameters": [integer(name="not_common_id", required=True)],
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "parameters": [integer(name="not_common_id", required=True)],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
        parameters={"Param": {"in": "query", "name": "key", "required": True, "type": "integer"}},
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 20$"])


def test_common_parameters_with_references_stateful(ctx):
    responses = {"responses": {"200": {"description": "OK"}}}
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "parameters": [
                    {"$ref": "#/parameters/Param"},
                    {"schema": {"$ref": "#/definitions/SimpleIntRef"}, "in": "body", "name": "id", "required": True},
                ],
                "put": {"parameters": [integer(name="not_common_id", required=True)], **responses},
                "post": {
                    "operationId": "post-foo",
                    "parameters": [integer(name="not_common_id", required=True)],
                    **responses,
                },
            },
            "/bar": {
                "post": {
                    "responses": {
                        "200": {
                            "x-links": {
                                "FooPut": {"operationRef": "#/paths/~1foo/put", "parameters": {"not_common_id": 42}},
                                "FooPOST": {"operationId": "post-foo", "parameters": {"not_common_id": 42}},
                            },
                            "description": "OK",
                        }
                    }
                }
            },
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
        parameters={"Param": {"in": "query", "name": "key", "required": True, "type": "integer"}},
        basePath="/v1",
        version="2.0",
    )
    state_machine = schema.as_state_machine()
    assert len(state_machine.bundles) == 1
    assert "POST /bar -> 200" in state_machine.bundles
    # 1 operation that creates data for other operations + 2 links
    assert hasattr(state_machine, "POST_bar___200_FooPOST__POST_foo")
    assert hasattr(state_machine, "POST_bar___200_FooPut__PUT_foo")
    assert hasattr(state_machine, "RANDOM__POST_bar")


def test_common_parameters_multiple_tests(testdir):
    testdir.make_test(
        """
def impl(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method in ["GET", "POST"]
    if not hasattr(case.meta.phase.data, "description"):
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
        paths={
            "/users": {
                "parameters": [integer(name="common_id", required=True)],
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 12$"])
