import schemathesis
from schemathesis.generation.modes import GenerationMode

from .utils import integer


def test_common_parameters(testdir):
    # When common parameter is shared on an API operation level
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
    # Then this parameter should be used for all specified methods
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines([r"Hypothesis calls: 4$"])


def test_common_parameters_with_references(testdir):
    # When common parameter that is shared on an API operation level contains a reference
    # And this parameter is in `body`
    # And the schema is used for multiple test functions
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
    # Then this parameter should be used in all generated tests
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 8$"])


def test_common_parameters_with_references_stateful(ctx):
    # When common parameter that is shared on an API operation level contains a reference
    # And used in stateful tests
    responses = {"responses": {"200": {"description": "OK"}}}
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema)
    # Then state machine should be successfully generated
    state_machine = schema.as_state_machine()
    assert len(state_machine.bundles) == 1
    assert "POST /bar -> 200" in state_machine.bundles
    # 1 operation that creates data for other operations + 2 links
    assert hasattr(state_machine, "POST_bar___200_FooPOST__POST_foo")
    assert hasattr(state_machine, "POST_bar___200_FooPut__PUT_foo")
    assert hasattr(state_machine, "RANDOM__POST_bar")


def test_common_parameters_multiple_tests(testdir):
    # When common parameters are specified on an API operation level
    # And the same schema is used in multiple tests
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
    # Then this parameter should be used in all test functions
    result.assert_outcomes(passed=4)
    result.stdout.re_match_lines([r"Hypothesis calls: 8$"])
    # NOTE: current implementation requires a deepcopy of the whole schema
