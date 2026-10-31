from __future__ import annotations

import pytest
from flask import jsonify

import schemathesis
from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.run import PhaseName
from schemathesis.engine.supervisor import METHOD_NOT_ALLOWED_THRESHOLD
from schemathesis.generation import GenerationMode
from test.apps.catalog.grpc.descriptors import load


def method_not_allowed_schema(ctx):
    return schemathesis.openapi.from_url(ctx.openapi.apps.unimplemented_method().schema_url)


def unimplemented_schema(ctx):
    # The server implements `Envelope` only, so `Scalars` answers UNIMPLEMENTED.
    return load(ctx.grpc.apps.envelope()).include(name=["types.Types/Scalars", "types.Types/Envelope"])


@pytest.mark.parametrize(
    ("load_schema", "missing", "served", "warning", "status"),
    [
        (
            method_not_allowed_schema,
            "POST /missing",
            "GET /items",
            SchemathesisWarning.METHOD_NOT_ALLOWED,
            "405 Method Not Allowed",
        ),
        (
            unimplemented_schema,
            "types.Types/Scalars",
            "types.Types/Envelope",
            SchemathesisWarning.NOT_SERVED,
            "UNIMPLEMENTED",
        ),
    ],
    ids=["method-not-allowed", "unimplemented"],
)
def test_unserved_operation_skipped_in_later_phases(ctx, load_schema, missing, served, warning, status):
    schema = load_schema(ctx)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=10)

    fuzzing_missing_skipped = 0
    fuzzing_missing_ran = 0
    fuzzing_served_ran = 0
    skip_reasons: dict[str, str | None] = {}
    for event in from_schema(schema).execute():
        if not isinstance(event, events.ScenarioFinished):
            continue
        if event.skip_warning is warning and event.label is not None:
            skip_reasons[event.label] = event.skip_reason
        if event.phase is not PhaseName.FUZZING:
            continue
        if event.label == missing:
            if event.status is Status.SKIP:
                fuzzing_missing_skipped += 1
            else:
                fuzzing_missing_ran += 1
        elif event.label == served and event.status is not Status.SKIP:
            fuzzing_served_ran += 1

    assert fuzzing_missing_ran == 0, f"Expected {missing} fuzzing to be entirely skipped, but {fuzzing_missing_ran} ran"
    assert fuzzing_missing_skipped >= 1, f"Expected at least one synthetic SKIP event for {missing} in fuzzing"
    assert fuzzing_served_ran > 0, f"Expected {served} fuzzing to run normally, got {fuzzing_served_ran}"
    assert skip_reasons == {missing: f"Skipped after {METHOD_NOT_ALLOWED_THRESHOLD} consecutive `{status}` responses"}


def test_baked_cases_short_circuit_after_supervisor_fires(ctx):
    # Eager-baked Coverage cases past the supervisor's fire point must skip
    # the server call instead of running the full mutation set.
    api = ctx.openapi.apps.unimplemented_method()
    store = api.wsgi_app.config["store"]
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=50)

    list(from_schema(schema).execute())

    assert store.hits["missing"] >= METHOD_NOT_ALLOWED_THRESHOLD, (
        f"streak threshold should have been reached; got {store.hits['missing']} hits"
    )
    # Without the short-circuit: 50 Fuzzing examples + Coverage mutations would all run.
    assert store.hits["missing"] <= METHOD_NOT_ALLOWED_THRESHOLD + 5, (
        f"Expected server calls bounded after supervisor fires; got {store.hits['missing']} hits"
    )
    assert store.hits["items"] > 0, "GET /items should still be exercised"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_method_not_allowed_warning_in_cli_output(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.unimplemented_method()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=examples,coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


def test_stateful_skips_supervisor_blocked_operations(ctx):
    # Stateful's `TransitionController` must reject rules whose target operation
    # has a SKIP verdict so the dead op is never selected as a transition.
    api = ctx.openapi.apps.linked_with_unimplemented_method()
    store = api.wsgi_app.config["store"]
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "stateful"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    missing_hits_pre_stateful = 0
    saw_stateful_finish = False
    for event in from_schema(schema).execute():
        if isinstance(event, events.PhaseStarted) and event.phase.name == PhaseName.STATEFUL_TESTING:
            missing_hits_pre_stateful = store.hits["missing"]
        if isinstance(event, events.PhaseFinished) and event.phase.name == PhaseName.STATEFUL_TESTING:
            saw_stateful_finish = True

    assert saw_stateful_finish, "Stateful phase did not run"
    assert missing_hits_pre_stateful >= METHOD_NOT_ALLOWED_THRESHOLD, (
        f"streak should have triggered before Stateful started; got {missing_hits_pre_stateful} pre-Stateful hits"
    )
    stateful_missing_hits = store.hits["missing"] - missing_hits_pre_stateful
    assert stateful_missing_hits == 0, (
        f"Stateful sent {stateful_missing_hits} requests to POST /missing despite the SKIP verdict"
    )
    assert store.hits["items_id"] > 0, "Stateful did not chain GET /items/{itemId} from the link"


def test_stateful_only_run_stops_calling_persistently_405_operations(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
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
                                "DeleteItem": {
                                    "operationId": "deleteItem",
                                    "parameters": {"itemId": "$response.body#/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/items/{itemId}": {
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "Deleted"}},
                }
            },
        }
    )
    hits = {"delete": 0}

    @app.route("/items", methods=["POST"])
    def create_item():
        return jsonify({"id": 1}), 201

    @app.route("/items/<item_id>", methods=["DELETE"])
    def delete_item(item_id):
        hits["delete"] += 1
        return jsonify({"error": "Method Not Allowed"}), 405

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["stateful"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=50)

    list(from_schema(schema).execute())

    assert hits["delete"] == METHOD_NOT_ALLOWED_THRESHOLD
