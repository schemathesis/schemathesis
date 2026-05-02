from __future__ import annotations

import pytest
from flask import jsonify

import schemathesis
from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode


@pytest.fixture
def unimplemented_method_app(ctx, app_runner):
    paths = {
        "/missing": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}, "size": {"type": "integer"}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/items": {"get": {"responses": {"200": {"description": "OK"}}}},
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/missing", methods=["POST"])
    def missing_post():
        return jsonify({"error": "Method Not Allowed"}), 405

    @app.route("/items", methods=["GET"])
    def items_get():
        return jsonify({"ok": True}), 200

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


def test_persistent_405_skips_op_in_later_phases(unimplemented_method_app):
    schema = schemathesis.openapi.from_url(unimplemented_method_app)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=10)

    missing = "POST /missing"
    items = "GET /items"
    fuzzing_missing_skipped = 0
    fuzzing_missing_ran = 0
    fuzzing_items_ran = 0
    method_not_allowed_skip_reasons: dict[str, str | None] = {}
    for event in from_schema(schema).execute():
        if not isinstance(event, events.ScenarioFinished):
            continue
        if event.skip_warning is SchemathesisWarning.METHOD_NOT_ALLOWED and event.label is not None:
            method_not_allowed_skip_reasons[event.label] = event.skip_reason
        if event.phase is not PhaseName.FUZZING:
            continue
        if event.label == missing:
            if event.status is Status.SKIP:
                fuzzing_missing_skipped += 1
            else:
                fuzzing_missing_ran += 1
        elif event.label == items and event.status is not Status.SKIP:
            fuzzing_items_ran += 1

    assert fuzzing_missing_ran == 0, f"Expected {missing} fuzzing to be entirely skipped, but {fuzzing_missing_ran} ran"
    assert fuzzing_missing_skipped >= 1, f"Expected at least one synthetic SKIP event for {missing} in fuzzing"
    assert fuzzing_items_ran > 0, f"Expected {items} fuzzing to run normally, got {fuzzing_items_ran}"
    assert missing in method_not_allowed_skip_reasons, (
        f"Expected `method_not_allowed` skip_warning surfaced for {missing}; got {method_not_allowed_skip_reasons}"
    )
    reason = method_not_allowed_skip_reasons[missing]
    assert reason and "405" in reason, f"Expected skip_reason to mention 405, got {reason!r}"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_method_not_allowed_warning_in_cli_output(cli, unimplemented_method_app, snapshot_cli):
    assert (
        cli.run(
            unimplemented_method_app,
            "--max-examples=10",
            "--phases=examples,coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


def test_method_not_allowed_warning_can_fail_the_run(cli, unimplemented_method_app):
    result = cli.run(
        unimplemented_method_app,
        "--max-examples=10",
        "--phases=examples,coverage,fuzzing",
        "--mode=positive",
        "--continue-on-failure",
        config={"warnings": {"fail-on": ["method_not_allowed"]}},
    )
    assert result.exit_code == 1, result.stdout
    assert "Method Not Allowed" in result.stdout
