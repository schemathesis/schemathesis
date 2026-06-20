from __future__ import annotations

import pytest
from _pytest.main import ExitCode


@pytest.mark.parametrize(
    ("make_app", "flags", "message"),
    [
        (lambda apps: apps.success(), (), "seen a response"),
        (lambda apps: apps.success_and_failure(), ("--max-examples=5", "--workers=2"), "ran under workers"),
    ],
    ids=["single-worker", "multiple-workers"],
)
def test_class_response_check_fails_run(ctx, cli, restore_checks, make_app, flags, message):
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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_after_run_failure_rendering(ctx, cli, snapshot_cli, ensure_reachability_module):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.main(
            "run",
            api.schema_url,
            "-c",
            "EnsureReachability",
            "--max-examples=5",
            "--phases=fuzzing",
            hooks=ensure_reachability_module,
        )
        == snapshot_cli
    )
