import pytest

from test.utils import allure_results


def _allure_results(allure_dir):
    return {
        result["name"]: (result["status"], (result.get("statusDetails", {}).get("message") or "").partition("\n")[0])
        for result in allure_results(allure_dir)
    }


@pytest.mark.parametrize("shape", ["parametrized", "from_fixture", "stateful"])
def test_check_failures_are_failed(testdir, tmp_path, ctx, shape):
    allure_dir = tmp_path / "allure-results"
    if shape == "stateful":
        api = ctx.openapi.apps.users_crud()
        testdir.makepyfile(
            f"""
import schemathesis
from hypothesis import settings

schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.update(base_url="{api.base_url}")
TestCase = schema.as_state_machine().TestCase
TestCase.settings = settings(max_examples=10, stateful_step_count=3, deadline=None, derandomize=True)

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
