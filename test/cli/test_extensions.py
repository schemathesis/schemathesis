import re

import pytest
from flask import jsonify, request

RECORDER = """
from schemathesis.engine import events


@schemathesis.extension("recorder")
class Recorder:
    def __init__(self, settings):
        self.label = settings["labels"][0]
        self.seen = []

    def setup(self, context, schema):
        self.seen.append("setup")

    def after_call(self, context, case, response):
        if self.seen[-1] != "call":
            self.seen.append("call")

    def teardown(self, context, schema):
        self.seen.append("teardown")

    def handle_event(self, ctx, event):
        if isinstance(event, events.EngineFinished):
            ctx.add_summary_line(f"Recorder [{self.label}]: {', '.join(self.seen)}")
"""


@pytest.mark.parametrize("args", [("run", "--max-examples=1"), ("fuzz", "--max-time=1")], ids=["run", "fuzz"])
def test_extension_settings_and_state_span_the_run(ctx, cli, args):
    api = ctx.openapi.apps.success()
    result = cli.main(
        args[0],
        api.schema_url,
        args[1],
        config={"hooks": ctx.write_pymodule(RECORDER), "extensions": {"recorder": {"labels": ["${LABEL}"]}}},
        env={"LABEL": "staging"},
    )
    assert "Recorder [staging]: setup, call, teardown" in result.stdout, result.stdout


def test_extensions_run_after_plain_hooks_in_load_order(ctx, cli):
    api = ctx.openapi.apps.success()
    first = ctx.write_pymodule(
        """
CALLS = []


@schemathesis.extension("first")
class First:
    def __init__(self, settings):
        pass

    def setup(self, context, schema):
        CALLS.append("first")
""",
        filename="first",
    )
    second = ctx.write_pymodule(
        """
from first import CALLS
from schemathesis.engine import events


@schemathesis.hook
def setup(context, schema):
    CALLS.append("plain")


@schemathesis.extension("second")
class Second:
    def __init__(self, settings):
        pass

    def setup(self, context, schema):
        CALLS.append("second")

    def handle_event(self, ctx, event):
        if isinstance(event, events.EngineFinished):
            ctx.add_summary_line(f"Setup order: {', '.join(CALLS)}")
""",
        filename="second",
    )
    result = cli.main("run", api.schema_url, "--max-examples=1", config={"hooks": [first, second]})
    assert "Setup order: plain, first, second" in result.stdout, result.stdout


def test_each_run_gets_a_fresh_extension(ctx, cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
from schemathesis.engine import events

SETUPS = []


@schemathesis.extension("counter")
class Counter:
    def __init__(self, settings):
        self.setups = 0

    def setup(self, context, schema):
        self.setups += 1
        SETUPS.append(self)

    def handle_event(self, ctx, event):
        if isinstance(event, events.EngineFinished):
            ctx.add_summary_line(f"Setups: {self.setups} here, {len(SETUPS)} in total")


schemathesis.extension("counter")(Counter)
"""
    )
    outputs = [cli.main("run", api.schema_url, "--max-examples=1", hooks=module).stdout for _ in range(2)]
    assert [re.search(r"Setups: .*", output).group() for output in outputs] == [
        "Setups: 1 here, 1 in total",
        "Setups: 1 here, 2 in total",
    ]


TAINT = """
@schemathesis.extension("taint")
class Taint:
    def __init__(self, settings):
        if settings.get("depth", 1) < 1:
            raise ValueError("`depth` must be positive")
"""


@pytest.mark.parametrize(
    ("source", "extensions"),
    [
        (TAINT, {"tiant": {}}),
        ("", {"taint": {}}),
        (TAINT, {"taint": {"depth": 0}}),
        (TAINT, {"taint": {"depth": "3"}}),
    ],
    ids=["unknown-table", "module-not-loaded", "rejected-settings", "constructor-bug"],
)
def test_extension_startup_errors(ctx, cli, snapshot_cli, source, extensions):
    api = ctx.openapi.apps.success()
    config = {"hooks": ctx.write_pymodule(source), "extensions": extensions}
    assert cli.main("run", api.schema_url, config=config) == snapshot_cli


@pytest.mark.parametrize(
    "source",
    [
        """
@schemathesis.extension("taint")
class Taint:
    def after_cal(self, context, case, response):
        pass
""",
        """
@schemathesis.extension("taint")
class Taint:
    def after_call(self, context, case):
        pass
""",
        """
@schemathesis.extension("taint")
class First:
    def __init__(self, settings):
        pass


@schemathesis.extension("taint")
class Second:
    def __init__(self, settings):
        pass
""",
        """
@schemathesis.extension
class Taint:
    def __init__(self, settings):
        pass
""",
        """
@schemathesis.extension("taint")
def taint(settings):
    pass
""",
        """
@schemathesis.extension("taint")
class Taint:
    def setup(self, context, schema):
        pass
""",
        """
@schemathesis.extension("taint")
class Taint:
    def __init__(self, settings):
        pass

    def handle_event(self, event):
        pass
""",
        """
class Taint:
    def __init__(self, settings):
        pass


schemathesis.extension("first")(Taint)
schemathesis.extension("second")(Taint)
""",
    ],
    ids=[
        "not-a-hook",
        "wrong-signature",
        "name-taken",
        "missing-name",
        "not-a-class",
        "no-settings-argument",
        "wrong-event-handler-signature",
        "class-registered-twice",
    ],
)
def test_invalid_extension_fails_to_load(ctx, cli, snapshot_cli, source):
    api = ctx.openapi.apps.success()
    assert cli.main("run", api.schema_url, hooks=ctx.write_pymodule(source)) == snapshot_cli


def test_extension_hook_error_names_the_extension(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.extension("taint")
class Taint:
    def __init__(self, settings):
        pass

    def setup(self, context, schema):
        raise ValueError("setup failed")
"""
    )
    assert cli.main("run", api.schema_url, "--max-examples=1", hooks=module) == snapshot_cli


def test_extension_event_handler_error_names_the_extension(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.extension("taint")
class Taint:
    def __init__(self, settings):
        pass

    def handle_event(self, ctx, event):
        raise ValueError("handler failed")
"""
    )
    assert cli.main("run", api.schema_url, "--max-examples=1", hooks=module) == snapshot_cli


def test_extension_methods_without_a_signature(ctx, cli, snapshot_cli):
    # Native callables may expose no signature; `slice` stands in for one.
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.extension("native")
class Native:
    def __init__(self, settings):
        pass

    setup = slice
    handle_event = slice
"""
    )
    assert cli.main("run", api.schema_url, "--max-examples=1", hooks=module) == snapshot_cli


def _code_app(ctx, seen):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/check": {
                "get": {
                    "parameters": [{"name": "code", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/check")
    def check():
        code = request.args.get("code")
        seen.append(code)
        # Fuzzing stops at the first failure, so the suggested value ends the run instead of the time limit.
        if code == "SECRET42":
            return jsonify({"error": "boom"}), 500
        return jsonify({})

    return app


SUGGEST_IN_SETUP = """
@schemathesis.extension("codes")
class Codes:
    def __init__(self, settings):
        pass

    def setup(self, context, schema):
        context.suggest(["SECRET42"], parameter="query.code")
"""
SUGGEST_AFTER_CALL = """
@schemathesis.extension("codes")
class Codes:
    def __init__(self, settings):
        pass

    def after_call(self, context, case, response):
        context.suggest(["SECRET42"], parameter="query.code")
"""


@pytest.mark.parametrize(
    "args",
    [("run", "--max-examples=30"), ("fuzz", "--max-time=30", "-c", "not_a_server_error")],
    ids=["run", "fuzz"],
)
def test_values_suggested_in_setup_reach_requests(ctx, cli, app_runner, args):
    seen = []
    url = app_runner.openapi_url(_code_app(ctx, seen))
    cli.main(args[0], url, *args[1:], hooks=ctx.write_pymodule(SUGGEST_IN_SETUP))
    assert "SECRET42" in seen


def test_values_suggested_during_fuzzing_reach_later_requests(ctx, cli, app_runner):
    seen = []
    url = app_runner.openapi_url(_code_app(ctx, seen))
    cli.main("fuzz", url, "--max-time=30", "-c", "not_a_server_error", hooks=ctx.write_pymodule(SUGGEST_AFTER_CALL))
    assert "SECRET42" in seen


def test_suggested_value_exposes_a_failure(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/check": {
                "get": {
                    "parameters": [{"name": "code", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/check")
    def check():
        if request.args.get("code") == "SECRET42":
            return jsonify({"error": "boom"}), 500
        return jsonify({})

    module = ctx.write_pymodule(SUGGEST_AFTER_CALL)
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=30",
            # Shrinking this seed's failure replays a case drawn before the value was suggested.
            "--seed=8",
            "--phases=fuzzing",
            "-c",
            "not_a_server_error",
            hooks=module,
        )
        == snapshot_cli
    )


def test_report_names_the_extension_that_suggested_a_value(ctx, cli, app_runner, tmp_path):
    report = tmp_path / "report.ndjson"
    url = app_runner.openapi_url(_code_app(ctx, []))
    cli.main(
        "run",
        url,
        "--max-examples=30",
        "--phases=fuzzing",
        f"--report-ndjson-path={report}",
        hooks=ctx.write_pymodule(SUGGEST_IN_SETUP),
    )
    assert re.search(r'"dictionary":\s*"codes",\s*"source_kind":\s*"suggestion"', report.read_text())
