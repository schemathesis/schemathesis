import pytest
from _pytest.main import ExitCode


def test_before_call(ctx, cli):
    api = ctx.openapi.apps.success()
    # When the `before_call` hook is registered
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, **kwargs):
    1 / 0
        """
    )
    result = cli.main("run", api.schema_url, hooks=module)
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then it should be called before each `case.call`
    assert "division by zero" in result.stdout


def test_before_call_no_kwargs_unpacking(ctx, cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, kwargs):
    kwargs["allow_redirects"] = False
        """
    )
    result = cli.main("run", api.schema_url, hooks=module)
    assert result.exit_code == ExitCode.OK, result.stdout


def test_after_call(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    # When the `after_call` hook is registered
    # And it modifies the response and making it incorrect
    module = ctx.write_pymodule(
        """
import requests

@schemathesis.hook
def after_call(context, case, response):
    data = b'{"wrong": 42}'
    response.content = data
        """
    )
    # Then the tests should fail
    assert cli.main("run", api.schema_url, "-c", "all", hooks=module) == snapshot_cli


def test_hook_execution_error(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    # When a hook raises an exception during schema initialization
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_init_operation(context, operation):
    raise AttributeError("test hook error")
        """
    )
    # Then it should be reported as a hook error, not a schema error
    assert cli.main("run", api.schema_url, hooks=module) == snapshot_cli


def test_hooks_file_path(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    # When SCHEMATHESIS_HOOKS points to an absolute file path
    hooks_file = tmp_path / "my_hooks.py"
    hooks_file.write_text("""
import schemathesis
@schemathesis.hook
def before_call(context, case, **kwargs):
    1 / 0
""")
    result = cli.main("run", api.schema_url, env={"SCHEMATHESIS_HOOKS": str(hooks_file)})
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "division by zero" in result.stdout


def test_hooks_file_path_unloadable(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    # When SCHEMATHESIS_HOOKS points to a file path with an unknown extension
    # that Python cannot determine a loader for (spec is None)
    hooks_file = tmp_path / "my_hooks.xyz"
    hooks_file.write_text("# hooks")
    result = cli.main("run", api.schema_url, env={"SCHEMATHESIS_HOOKS": str(hooks_file)})
    assert result.exit_code == 1, result.stdout
    assert "Unable to load Schemathesis extension hooks" in result.stdout
    assert "Cannot load hooks from:" in result.stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_filter_case_rejects_all(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    # When the `filter_case` hook rejects all generated test cases
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_case(context, case):
    return False
"""
    )
    # Then it should be reported as a hook error, not a schema error
    assert cli.main("run", api.schema_url, "--max-examples=10", hooks=module) == snapshot_cli
