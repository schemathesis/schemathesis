import pytest

from .utils import as_param


def test_required_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=20, suppress_health_check=[HealthCheck.data_too_large])
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "POST"
    assert "id" in case.body
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "object",
                            "required": True,
                            "schema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 20$"])


def test_not_required_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/users"
    assert case.method == "GET"
""",
        **as_param({"in": "query", "name": "key", "required": False, "type": "string"}),
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


@pytest.mark.parametrize(
    "spec_version, param",
    (
        ("open_api_2", {"in": "query", "name": "key", "type": "string"}),
        ("open_api_2", {"in": "body", "name": "body", "schema": {"type": "string"}}),
        ("open_api_3", {"in": "query", "name": "key", "schema": {"type": "string"}}),
        ("open_api_3", {"content": {"application/json": {"schema": {"type": "string"}}}}),
    ),
)
def test_without_required(request, testdir, spec_version, param):
    # When "required" field is not present in the parameter
    schema = request.getfixturevalue(f"empty_{spec_version}_schema")
    if spec_version == "open_api_2":
        schema["paths"] = {"/users": {"post": {"parameters": [param], "responses": {"200": {"description": "OK"}}}}}
        location = param["in"]
    else:
        path = {"responses": {"200": {"description": "OK"}}}
        if "content" in param:
            path["requestBody"] = param
            location = "body"
        else:
            path["parameters"] = [param]
            location = param["in"]
        schema["paths"] = {"/users": {"post": path}}
    testdir.make_test(
        f"""
@schema.parametrize()
@settings(max_examples=10)
def test_has_none(request, case):
    # This test should find `NOT_SET` values
    if "{location}" == "body":
        assert case.{location} is not NOT_SET
    else:
        assert case.{location}.get("key") is not None

@schema.parametrize()
@settings(max_examples=10)
def test_has_not_only_none(request, case):
    # This test should find values other than `NOT_SET`
    if "{location}" == "body":
        assert case.{location} is NOT_SET
    else:
        assert case.{location}.get("key") is None
""",
        schema=schema,
    )
    # then the parameter is optional
    # NOTE. could be flaky
    result = testdir.runpytest("-v", "-s")
    # First test should fail because it finds `None`
    # Second one fails because it finds values other than `None`
    result.assert_outcomes(failed=2)
