from __future__ import annotations

import pytest
from _pytest.main import ExitCode
from flask import jsonify, request

from test.utils import allure_results


@pytest.mark.parametrize(
    ("make_app", "flags", "message"),
    [
        (lambda apps: apps.success(), (), "seen a response"),
        (lambda apps: apps.success_and_failure(), ("--max-examples=5", "--workers=2"), "ran under workers"),
    ],
    ids=["single-worker", "multiple-workers"],
)
def test_class_response_check_fails_run(ctx, cli, make_app, flags, message):
    api = make_app(ctx.openapi.apps)
    module = ctx.write_pymodule(
        f'''
@schemathesis.check
class FailEveryResponse:
    def after_response(self, ctx, response, case):
        raise AssertionError("{message}")
        '''
    )
    result = cli.main("run", api.schema_url, "-c", "FailEveryResponse", *flags, hooks=module)
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert message in result.stdout


def test_after_run_failure_rendering(ctx, cli, snapshot_cli, ensure_reachability_module, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    allure_dir = tmp_path / "allure-results"
    assert (
        cli.main(
            "run",
            api.schema_url,
            "-c",
            "EnsureReachability",
            "--max-examples=5",
            "--phases=fuzzing",
            f"--report-allure-path={allure_dir}",
            hooks=ensure_reachability_module,
        )
        == snapshot_cli
    )
    results = allure_results(allure_dir)
    run_check_results = [r for r in results if r.get("name") == "Run checks"]
    assert run_check_results, [r.get("name") for r in results]
    assert run_check_results[0]["status"] == "failed"


@pytest.mark.snapshot(replace_reproduce_with=False)
def test_extra_request_to_candidate_api(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/value": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/value")
    def old_value():
        return jsonify({"version": "old", "auth": request.headers.get("Authorization")})

    @app.route("/v2/value")
    def candidate_value():
        return jsonify({"version": "new", "auth": request.headers.get("Authorization")})

    base_url = app_runner.openapi_url(app, path="")
    module = ctx.write_pymodule(
        f'''
@schemathesis.check
class Differential:
    def after_response(self, ctx, response, case):
        candidate = ctx.send(case, base_url="{base_url}/v2", headers={{"Authorization": "Bearer candidate"}})
        assert candidate.json() == response.json(), f"Old {{response.json()}}, candidate {{candidate.json()}}"
        '''
    )
    assert (
        cli.run(
            f"{base_url}/openapi.json",
            "-c",
            "Differential",
            "--phases=fuzzing",
            "--max-examples=1",
            "--header=Authorization: Bearer old",
            hooks=module,
        )
        == snapshot_cli
    )
