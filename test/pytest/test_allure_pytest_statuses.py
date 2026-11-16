import re
from xml.etree import ElementTree

import pytest
from flask import jsonify, request

from test.utils import allure_results


def _allure_results(allure_dir):
    return {
        result["name"]: (result["status"], (result.get("statusDetails", {}).get("message") or "").partition("\n")[0])
        for result in allure_results(allure_dir)
    }


def _two_thresholds_schema_url(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"price": {"type": "integer"}},
                                    "required": ["price"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create_item():
        price = request.json["price"]
        if price > 100:
            return jsonify({}), 500
        if price < -100:
            return jsonify({}), 418
        return jsonify({"id": 1}), 201

    @app.route("/items/<int:item_id>")
    def get_item(item_id):
        return jsonify({})

    return app_runner.openapi_url(app)


def _reported_prices(text):
    return sorted(re.findall(r"-d '\{\"price\": (-?\d+)\}'", text))


@pytest.mark.parametrize("shape", ["parametrized", "from_fixture", "stateful"])
def test_distinct_check_failures_are_shrunk_and_failed(testdir, tmp_path, ctx, app_runner, shape):
    schema_url = _two_thresholds_schema_url(ctx, app_runner)
    allure_dir = tmp_path / "allure-results"
    junit_path = tmp_path / "junit.xml"
    settings = "settings(max_examples=10, derandomize=True, database=None, phases=[Phase.generate, Phase.shrink])"
    if shape == "parametrized":
        source = f"""
schema = schemathesis.openapi.from_url("{schema_url}")
schema.config.generation.update(modes=[GenerationMode.POSITIVE])
schema.config.reports.update(junit_path=r"{junit_path}")

@schema.include(method="POST").parametrize()
@{settings}
def test_api(case):
    case.call_and_validate()
"""
        name = "test_api[POST /items]"
    elif shape == "from_fixture":
        source = f"""
@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_url("{schema_url}").include(method="POST")
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    schema.config.reports.update(junit_path=r"{junit_path}")
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
@{settings}
def test_api(case):
    case.call_and_validate()
"""
        name = "test_api"
    else:
        source = f"""
schema = schemathesis.openapi.from_url("{schema_url}")
schema.config.generation.update(modes=[GenerationMode.POSITIVE])
schema.config.reports.update(junit_path=r"{junit_path}")
TestCase = schema.as_state_machine().TestCase
TestCase.settings = {settings[:-1]}, deadline=None, stateful_step_count=3)
"""
        name = "runTest"
    testdir.make_test(source)

    result = testdir.runpytest("--alluredir", str(allure_dir))

    junit_failures = "".join(failure.text for failure in ElementTree.parse(junit_path).iter("failure"))
    assert (_reported_prices(result.stdout.str()), _reported_prices(junit_failures)) == (
        ["-101", "101"],
        ["-101", "101"],
    )
    assert _allure_results(allure_dir) == {
        name: ("failed", "ExceptionGroup: Hypothesis found 2 distinct failures. (2 sub-exceptions)")
    }


@pytest.mark.parametrize("shape", ["parametrized", "from_fixture", "stateful"])
def test_check_failures_are_failed(testdir, tmp_path, ctx, shape):
    allure_dir = tmp_path / "allure-results"
    if shape == "stateful":
        api = ctx.openapi.apps.users_crud()
        testdir.makepyfile(
            f"""
import schemathesis
from hypothesis import Phase, settings

schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.update(base_url="{api.base_url}")
TestCase = schema.as_state_machine().TestCase
TestCase.settings = settings(
    max_examples=10, stateful_step_count=3, deadline=None, derandomize=True, phases=[Phase.generate]
)

def test_passing():
    pass
"""
        )
        expected = {
            "runTest": (
                "failed",
                "schemathesis.core.failures.FailureGroup: Schemathesis found 1 distinct failure",
            ),
            "test_passing": ("passed", ""),
        }
    else:
        api = ctx.openapi.apps.success_and_failure()
        if shape == "parametrized":
            testdir.make_test(
                f"""
schema = schemathesis.openapi.from_url("{api.schema_url}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
"""
            )
            expected = {
                "test_api[GET /api/failure]": (
                    "failed",
                    "schemathesis.core.failures.FailureGroup: Schemathesis found 1 distinct failure",
                ),
                "test_api[GET /api/success]": ("passed", ""),
            }
        else:
            testdir.make_test(
                f"""
@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_url("{api.schema_url}")

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()

def test_passing():
    pass
"""
            )
            expected = {
                "test_api": (
                    "failed",
                    "schemathesis.core.failures.FailureGroup: Schemathesis found 1 distinct failure",
                ),
                "test_passing": ("passed", ""),
            }

    result = testdir.runpytest("--alluredir", str(allure_dir))

    assert _allure_results(allure_dir) == expected
    assert "FailureGroup" in result.stdout.str()


def test_non_check_outcomes_are_unchanged(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    unsatisfiable_api = ctx.openapi.apps.unsatisfiable()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url("{api.schema_url}")
unsatisfiable = schemathesis.openapi.from_url("{unsatisfiable_api.schema_url}")
unsatisfiable.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.parametrize()
@settings(max_examples=1)
def test_runtime_error(case):
    case.call()
    raise RuntimeError("bug")

@schema.parametrize()
@settings(max_examples=1)
def test_skip(case):
    pytest.skip("x")

@schema.parametrize()
@settings(max_examples=1)
def test_assertion(case):
    case.call()
    assert False, "user"

@unsatisfiable.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_unsatisfiable(case):
    case.call_and_validate()
"""
    )

    testdir.runpytest("--alluredir", str(allure_dir))

    assert {name: result[0] for name, result in _allure_results(allure_dir).items()} == {
        "test_runtime_error[GET /api/success]": "broken",
        "test_skip[GET /api/success]": "skipped",
        "test_assertion[GET /api/success]": "failed",
        "test_unsatisfiable[POST /api/unsatisfiable]": "broken",
    }


def test_failure_group_can_be_caught_in_test_body(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.failure()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url("{api.schema_url}")

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    with pytest.raises(schemathesis.errors.FailureGroup):
        case.call_and_validate()
"""
    )

    result = testdir.runpytest("--alluredir", str(allure_dir))

    result.assert_outcomes(passed=1)
    assert _allure_results(allure_dir) == {"test_api[GET /api/failure]": ("passed", "")}
