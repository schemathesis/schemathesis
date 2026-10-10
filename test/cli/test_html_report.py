import itertools
import re
import time

import pytest
from flask import jsonify
from hypothesis import strategies as st

import schemathesis
from schemathesis.cli.constants import ExitCode
from schemathesis.graphql import nodes
from schemathesis.reporting.html.render import humanize_duration
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS


@pytest.fixture
def report_dir(tmp_path):
    return tmp_path / "report"


def run_with_report(cli, report_dir, *args, exit_code=ExitCode.OK, phases="fuzzing", **kwargs):
    cli.run_and_assert(
        *args,
        "--max-examples=1",
        f"--phases={phases}",
        "--seed=42",
        exit_code=exit_code,
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
        **kwargs,
    )
    return (report_dir / "index.html").read_text(encoding="utf-8")


def test_html_report_passed(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    assert run_with_report(cli, report_dir, api.schema_url) == snapshot_html
    assert (report_dir / "assets" / "report.css").is_file()


def test_html_report_failed(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.failure()
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_schema_not_loaded(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    # Serves JSON that is not an API schema.
    url = f"{api.base_url}/api/success"
    assert run_with_report(cli, report_dir, url, exit_code=ExitCode.ERROR) == snapshot_html


def test_html_report_no_operations_selected(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "--include-path=/unknown",
        "--include-method=PURGE",
        exit_code=ExitCode.ERROR,
    )
    assert html == snapshot_html


def test_html_report_stopped_at_failure_limit(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--max-failures=1", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


def test_html_report_schema_without_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({})
    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.ERROR) == snapshot_html


def test_html_report_schema_unreachable_after_waiting(cli, report_dir, snapshot_html):
    # The terminal drops the "wait for the schema" tip once the run already waited.
    html = run_with_report(
        cli, report_dir, "http://127.0.0.1:1/openapi.json", "--wait-for-schema=1", exit_code=ExitCode.ERROR
    )
    assert html == snapshot_html


def test_html_report_schema_loading_hook_error(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_load_schema(ctx, raw_schema):
    raise ValueError("Broken hook")
"""
    )
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.ERROR, hooks=module) == snapshot_html


def test_html_report_interrupted_before_any_case(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    raise KeyboardInterrupt
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.INTERRUPTED, hooks=module)
    assert html == snapshot_html


def test_html_report_interrupted_with_results(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(
        """
CALLS = []


@schemathesis.hook
def before_call(ctx, case, **kwargs):
    # The first operation completes; Ctrl-C lands on the second.
    CALLS.append(case)
    if len(CALLS) > 1:
        raise KeyboardInterrupt
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.INTERRUPTED, hooks=module)
    assert html == snapshot_html


def test_html_report_failure_outside_tested_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/users": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    # Accepting a method the schema does not declare fails on that method, not on a tested operation.
    @app.route("/api/items", methods=["GET", "TRACE"])
    @app.route("/api/users", methods=["GET", "TRACE"])
    def items():
        return jsonify({})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=unsupported_method",
        phases="coverage",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html


INVALID_BODY = {
    "post": {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "integer", "minimum": "abc"}}},
        },
        "responses": {"200": {"description": "OK"}},
    }
}


def test_html_report_every_operation_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/broken": INVALID_BODY})
    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_some_operations_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {"/api/broken": INVALID_BODY, "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}}}
    )

    @app.route("/api/success")
    def success():
        return "{}"

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_operations_table(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/broken": INVALID_BODY,
        }
    )

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_continue_on_failure(ctx, cli, report_dir, snapshot_html):
    # Failed operations keep running past their first failure, so their case counts need no explanation.
    api = ctx.openapi.apps.failure()
    html = run_with_report(cli, report_dir, api.schema_url, "--continue-on-failure", exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_skipped_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/with-example": {
                "get": {"parameters": [{**parameter, "example": "pets"}], "responses": {"200": {"description": "OK"}}}
            },
            "/api/without-example": {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/with-example")
    def with_example():
        return "{}"

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), phases="examples") == snapshot_html


def test_html_report_fuzz_failures(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    result = cli.main(
        "fuzz",
        api.schema_url,
        "--max-failures=1",
        "--max-time=5",
        "--seed=42",
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
    )
    assert result.exit_code == ExitCode.FAILURES, result.stdout
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    # Fuzzing runs operations concurrently, so only the failed row is stable.
    assert '<tr class="op-row row-failed has-details" id="op-get-api-failure">' in html
    assert '<span class="method get">GET</span><span class="path">/api<wbr>/failure</span>' in html
    assert '<tr class="detail-row detail-failed" id="op-get-api-failure-details">' in html


# `/api/items-0` and `/api/items/0` share an anchor slug.
def test_html_report_large_api(ctx, cli, app_runner, report_dir, snapshot_html):
    paths = {f"/api/items/{index}": {"get": {"responses": {"200": {"description": "OK"}}}} for index in range(26)}
    paths["/api/failure"] = {"get": {"responses": {"200": {"description": "OK"}}}}
    paths["/api/items-0"] = {"get": {"responses": {"200": {"description": "OK"}}}}
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/api/items/<index>")
    def item(index):
        return "{}"

    @app.route("/api/items-0")
    def item_dash():
        return "{}"

    @app.route("/api/failure")
    def failure():
        return "", 500

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_graphql(ctx, cli, report_dir, snapshot_html):
    api = ctx.graphql.apps.books()
    assert run_with_report(cli, report_dir, api.schema_url) == snapshot_html


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1.23, "1.2s"), (59.96, "1m 0s"), (125.0, "2m 5s"), (7325.0, "2h 2m")],
    ids=["seconds", "rounds-into-minutes", "minutes", "hours"],
)
def test_humanize_duration(seconds, expected):
    assert humanize_duration(seconds) == expected


def test_html_report_fuzz_interrupted(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    raise KeyboardInterrupt
"""
    )
    result = cli.main(
        "fuzz",
        api.schema_url,
        "--max-time=2",
        "--seed=42",
        hooks=module,
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
    )
    assert result.exit_code == ExitCode.INTERRUPTED, result.stdout
    assert (report_dir / "index.html").read_text(encoding="utf-8") == snapshot_html


def test_html_report_baseline_not_written(ctx, cli, tmp_path, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    # The baseline's parent is a regular file, so saving the baseline fails after the run.
    parent = tmp_path / "file"
    parent.write_text("", encoding="utf-8")
    baseline = str(parent / "baseline.yaml")
    html = run_with_report(
        cli, report_dir, api.schema_url, "--baseline-update", exit_code=ExitCode.ERROR, config={"baseline": baseline}
    )
    # The message quotes the path with `repr`, which doubles Windows backslashes.
    assert html.replace(repr(baseline)[1:-1], "<BASELINE>") == snapshot_html


def test_html_report_no_checks_ran(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, config={"checks": {"enabled": False}})
    assert html == snapshot_html


def test_html_report_failure_details(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {"schema": {"type": "array", "items": {"type": "integer"}}}
                            },
                        }
                    }
                }
            },
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/items")
    def items():
        return jsonify([{"id": index, "name": f"item-{index}"} for index in range(20)])

    @app.route("/api/failure")
    def failure():
        return "Internal error\nRequest id: 42", 500

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


# Under an unknown shell, curl commands for invalid bodies end with warning lines.
def test_html_report_stateful_failure(ctx, cli, app_runner, report_dir, snapshot_html, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/unknown")
    item_id = {"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string", "enum": ["apple"]}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "links": {
                                "GetItem": {
                                    "operationId": "getItem",
                                    "parameters": {"item_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/api/items/{item_id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [item_id],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )
    created = []

    @app.route("/api/items", methods=["POST"])
    def create_item():
        created.append(len(created) + 1)
        return jsonify({"id": created[-1]}), 201

    @app.route("/api/items/<int:item_id>")
    def get_item(item_id):
        if item_id in created:
            return "", 500
        return jsonify({"detail": "Not found"}), 404

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        phases="stateful",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html
    assert "until first failure" not in html


def test_html_report_error_details(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    if case.path == "/api/failure":
        raise ValueError("Broken hook")
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES, hooks=module)
    assert html == snapshot_html


def test_html_report_network_error_details(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, "--url=http://127.0.0.1:1", exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_run_level_failure(ctx, cli, report_dir, snapshot_html, ensure_reachability_module):
    api = ctx.openapi.apps.success_and_failure()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "-c",
        "EnsureReachability",
        exit_code=ExitCode.FAILURES,
        hooks=ensure_reachability_module,
    )
    assert html == snapshot_html


# The check's extra request without credentials is the only one in the reproduction.
def test_html_report_ignored_auth(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {"/api/me": {"get": {"security": [{"bearer": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    )

    @app.route("/api/me")
    def me():
        return jsonify({"user": "alice"})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "-c",
        "ignored_auth",
        "-H",
        "Authorization: Bearer secret",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html


def test_html_report_timeout_error(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/slow": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/slow")
    def slow():
        time.sleep(1)
        return jsonify({})

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--request-timeout=0.1", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


# The unsatisfiable body errors only on positive cases, reached by continuing past the first failure.
def test_html_report_error_tip(ctx, cli, app_runner, report_dir, snapshot_html):
    body = {"allOf": [{"type": "string"}, {"type": "integer"}]}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/conflict": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--continue-on-failure", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


@pytest.fixture
def book_id_scalar():
    schemathesis.graphql.scalar("BookID", st.uuids().map(str).map(nodes.String))
    yield
    CUSTOM_SCALARS.clear()


# Generated titles differ between runs, so only the step labels are compared.
@pytest.mark.usefixtures("book_id_scalar")
def test_html_report_graphql_stateful_steps(ctx, cli, report_dir):
    api = ctx.graphql.apps.use_after_create()
    cli.run_and_assert(
        api.schema_url,
        "--max-examples=20",
        "--phases=stateful",
        "--mode=positive",
        "--checks=not_a_server_error",
        "--seed=42",
        exit_code=ExitCode.FAILURES,
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
    )
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    steps = re.findall(r'<span class="step-req">([^<]*)', html)
    assert steps
    assert all(re.fullmatch(r"(Query|Mutation)\.\w+", step) for step in steps), steps
    assert '<span class="step-req">Query.book<span class="step-failed">failed here</span>' in html


def test_html_report_graphql_unknown_scalar_tip(ctx, cli, report_dir, snapshot_html):
    api = ctx.graphql.apps.use_after_create()
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES) == snapshot_html


# Each phase times out on different parameters; timing varies the case count, so only error cards are compared.
def test_html_report_repeated_error_shown_once(ctx, cli, app_runner, report_dir):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {"/api/slow": {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}}}
    )

    @app.route("/api/slow")
    def slow():
        time.sleep(0.5)
        return jsonify({})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--request-timeout=0.1",
        phases="examples,coverage,fuzzing",
        exit_code=ExitCode.FAILURES,
    )
    assert html.count('<article class="case case-error">') == 1


def test_html_report_ids_are_unique(ctx, cli, app_runner, report_dir):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/users/details": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/users")
    def users():
        return "", 500

    @app.route("/api/users/details")
    def details():
        return "", 500

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    ids = re.findall(r' id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), sorted(id_ for id_ in ids if ids.count(id_) > 1)


def test_html_report_run_level_error(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "links": {
                                "Missing": {"operationId": "missing", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    }
                }
            },
        }
    )

    @app.route("/api/items", methods=["POST"])
    def create_item():
        return jsonify({"id": 1}), 201

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), phases="fuzzing,stateful", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


def test_html_report_warning_on_passed_run(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.basic()
    html = run_with_report(
        cli, report_dir, api.schema_url, "--checks=not_a_server_error", "--mode=positive", "--max-examples=10"
    )
    assert html == snapshot_html


def test_html_report_warning_grouped_by_cause(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.users_crud()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        f"--url={api.base_url}/v4/",
        "--checks=not_a_server_error",
        "--mode=positive",
        "--max-examples=10",
        phases="fuzzing,stateful",
    )
    assert html == snapshot_html


def test_html_report_warning_with_details(ctx, cli, app_runner, report_dir, snapshot_html):
    body = {"type": "array", "items": {"type": "string", "pattern": r"\p{Tibetan}"}, "maxItems": 3}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/tags": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/api/tags", methods=["POST"])
    def tags():
        return jsonify({})

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), "--checks=not_a_server_error")
    assert html == snapshot_html


def test_html_report_unmatched_filter_warning(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli, report_dir, api.schema_url, "--include-name=GET /api/success", "--include-name=GET /api/missing"
    )
    assert html == snapshot_html


def test_html_report_startup_warning(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    assert run_with_report(cli, report_dir, api.schema_url, "--request-timeout=2000") == snapshot_html


# Startup warnings come first, then warnings that affect the verdict, then setup notes.
def test_html_report_warnings_order(ctx, cli, app_runner, report_dir):
    order_id = {"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {"/api/orders/{order_id}": {"get": {"parameters": [order_id], "responses": {"200": {"description": "OK"}}}}}
    )
    calls = itertools.count()

    @app.route("/api/orders/<order_id>")
    def order(order_id):
        if next(calls) % 10 == 0:
            return jsonify({"id": order_id})
        return jsonify({"detail": "Not found"}), 404

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        "--mode=positive",
        "--max-examples=10",
        "--phases=fuzzing",
        "--seed=42",
        "--request-timeout=2000",
        "--include-path-regex=^/api/orders",
        "--include-name=GET /api/missing",
        "--warnings=low_valid_rate,unmatched_filter,timeout_units",
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
    )
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    entries = re.findall(r'<li class="(warning[^"]*)">(?:<span>|<details id="([^"]+)")', html)
    assert entries == [
        ("warning startup", ""),
        ("warning", "w-low-valid-input-rate"),
        ("warning note", "w-unmatched-filters"),
    ]
