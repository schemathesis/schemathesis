import pytest
from _pytest.main import ExitCode
from flask import jsonify
from hypothesis import settings


def test_before_call_no_kwargs_unpacking(ctx, cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, kwargs):
    kwargs["auth"] = ("hook", "called")
        """
    )
    result = cli.main("run", api.schema_url, hooks=module)
    assert result.exit_code == ExitCode.OK, result.stdout
    assert {request.headers.get("Authorization") for request in api.requests} == {"Basic aG9vazpjYWxsZWQ="}


@pytest.mark.parametrize("phase", ["fuzzing", "stateful"])
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
def test_before_call_system_exit(ctx, cli, snapshot_cli, phase):
    api = ctx.openapi.apps.users_crud()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, kwargs):
    raise SystemExit(1)
        """
    )
    assert cli.main("run", api.schema_url, f"--phases={phase}", "--max-examples=1", hooks=module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
def test_before_call_error_in_stateful_phase(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.users_crud()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, kwargs):
    raise ValueError("boom")
        """
    )
    assert cli.main("run", api.schema_url, "--phases=stateful", "--max-examples=1", hooks=module) == snapshot_cli


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


@pytest.fixture
def restore_hypothesis_profile():
    # Hooks in these tests register and load their own profiles; later tests must keep the suite's one.
    active = settings.get_current_profile_name()
    original = settings.get_profile("default")
    yield
    settings.register_profile("default", original)
    settings.load_profile(active)


@pytest.mark.usefixtures("restore_hypothesis_profile")
def test_hypothesis_deadline_from_hooks_is_reported(ctx, cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
import time
import hypothesis

hypothesis.settings.register_profile("tight", deadline=50)
hypothesis.settings.load_profile("tight")

@schemathesis.hook
def before_call(context, case, kwargs):
    time.sleep(0.15)
"""
    )
    result = cli.main("run", api.schema_url, "--phases=fuzzing", "--max-examples=2", hooks=module)
    assert "exceeds the deadline of 50.00ms" in result.stdout


@pytest.mark.usefixtures("restore_hypothesis_profile")
@pytest.mark.parametrize("max_examples", [3, 100])
def test_max_examples_option_wins_over_profile_from_hooks(ctx, cli, app_runner, max_examples):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({})

    module = ctx.write_pymodule(
        """
import hypothesis

hypothesis.settings.register_profile("large", max_examples=150)
hypothesis.settings.load_profile("large")
"""
    )
    result = cli.main(
        "run",
        app_runner.openapi_url(app),
        "--phases=fuzzing",
        "--mode=positive",
        f"--max-examples={max_examples}",
        hooks=module,
    )
    assert f"{max_examples} generated" in result.stdout


@pytest.mark.usefixtures("restore_hypothesis_profile")
def test_stateful_run_with_default_profile_settings(ctx, cli):
    api = ctx.openapi.apps.stateful_users()
    module = ctx.write_pymodule(
        """
from hypothesis import Phase, settings

settings.register_profile(
    "default", deadline=None, phases=[phase for phase in Phase if phase not in (Phase.explain, Phase.reuse)]
)
settings.load_profile("default")
"""
    )
    result = cli.main("run", api.schema_url, "--phases=stateful", "--mode=positive", "--max-examples=3", hooks=module)
    assert result.exit_code == ExitCode.OK, result.stdout


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


@pytest.mark.parametrize("invalid_schema", [{"pattern": "["}, {"minLength": "x"}], ids=["regex", "non-regex"])
@pytest.mark.snapshot(replace_reproduce_with=True, replace_traceback=True)
def test_before_generate_case_validation_error(ctx, cli, snapshot_cli, invalid_schema):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        f"""
import jsonschema_rs

@schemathesis.hook
def before_generate_case(context, strategy):
    jsonschema_rs.Draft202012Validator({invalid_schema!r})
    return strategy
"""
    )
    assert cli.main("run", api.schema_url, "--phases=fuzzing", "--max-examples=1", hooks=module) == snapshot_cli


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


def test_hooks_file_path_runs_once_per_call(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    calls = tmp_path / "calls.txt"
    hooks_file = tmp_path / "counting_hooks.py"
    hooks_file.write_text(f"""
import schemathesis
@schemathesis.hook
def before_call(context, case, **kwargs):
    with open({str(calls)!r}, "a") as fd:
        fd.write(".")
""")
    result = cli.main(
        "run",
        api.schema_url,
        "--phases=fuzzing",
        "--max-examples=3",
        env={"SCHEMATHESIS_HOOKS": str(hooks_file)},
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    api_calls = [request for request in api.requests if request.path.startswith("/api/")]
    assert len(calls.read_text()) == len(api_calls)


def test_hooks_file_path_unloadable(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    # When SCHEMATHESIS_HOOKS points to a file path with an unknown extension
    # that Python cannot determine a loader for (spec is None)
    hooks_file = tmp_path / "my_hooks.xyz"
    hooks_file.write_text("# hooks")
    result = cli.main("run", api.schema_url, env={"SCHEMATHESIS_HOOKS": str(hooks_file)})
    assert result.exit_code == 2, result.stdout
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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_filter_failure_drops_a_failure(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_failure(context, failure, case, response):
    return response.status_code != 500
"""
    )
    # `--max-failures=1` would cut the run short if a dropped failure still spent the budget.
    assert (
        cli.main(
            "run",
            api.schema_url,
            "-c",
            "not_a_server_error",
            "--max-examples=5",
            "--max-failures=1",
            "--phases=fuzzing",
            hooks=module,
        )
        == snapshot_cli
    )


def test_filter_failure_is_summarized_in_fuzz(ctx, cli):
    api = ctx.openapi.apps.failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_failure(context, failure, case, response):
    return response.status_code != 500
"""
    )

    result = cli.main("fuzz", api.schema_url, "-c", "not_a_server_error", "--max-time=1", hooks=module)

    assert "Filtered: " in result.stdout, result.stdout


def test_filter_failure_keeps_what_it_does_not_reject(ctx, cli):
    api = ctx.openapi.apps.failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_failure(context, failure, case, response):
    return response.status_code != 503
"""
    )
    result = cli.main("run", api.schema_url, "-c", "not_a_server_error", "--max-examples=1", hooks=module)
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout


def test_filter_failure_hook_error_is_reported(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_failure(context, failure, case, response):
    raise AttributeError("test hook error")
"""
    )
    assert cli.main("run", api.schema_url, "-c", "not_a_server_error", "--max-examples=1", hooks=module) == snapshot_cli


@pytest.mark.parametrize(
    ("target", "expected"),
    [("GET /api/failure", ExitCode.OK), ("GET /api/elsewhere", ExitCode.TESTS_FAILED)],
    ids=["applies", "does-not-apply"],
)
def test_filter_failure_respects_hook_filters(ctx, cli, target, expected):
    api = ctx.openapi.apps.failure()
    module = ctx.write_pymodule(
        f"""
@schemathesis.hook.apply_to(name="{target}")
def filter_failure(context, failure, case, response):
    return False
"""
    )
    result = cli.main("run", api.schema_url, "-c", "not_a_server_error", "--max-examples=1", hooks=module)
    assert result.exit_code == expected, result.stdout


@pytest.mark.parametrize(
    "filter_call",
    ['apply_to(name="GET /api/success")', 'skip_for(name="GET /api/failure")'],
    ids=["apply_to", "skip_for"],
)
def test_named_hook_respects_filters(ctx, cli, snapshot_cli, filter_call):
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(
        f"""
@schemathesis.hook("before_call").{filter_call}
def reject_call(context, case, kwargs):
    raise ValueError(f"Called for {{case.operation.label}}")
"""
    )
    assert cli.main("run", api.schema_url, "--phases=fuzzing", "--max-examples=1", hooks=module) == snapshot_cli


@pytest.mark.parametrize("command", ["run", "fuzz"])
@pytest.mark.parametrize(
    "hook",
    ["before_load_schema(context, raw_schema)", "after_load_schema(context, schema)"],
    ids=["before_load_schema", "after_load_schema"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_load_schema_hook_error(ctx, cli, snapshot_cli, command, hook):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        f"""
@schemathesis.hook
def {hook}:
    raise ValueError("boom")
"""
    )
    assert cli.main(command, api.schema_url, hooks=module) == snapshot_cli
