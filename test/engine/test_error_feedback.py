from __future__ import annotations

from flask import jsonify, request

import schemathesis
from schemathesis.engine import events, from_schema
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from test.apps.catalog.openapi import error_feedback as error_feedback_apps


def _collect_body_dates(schema, *, phase) -> list[str]:
    values: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == phase:
            for case_node in event.recorder.cases.values():
                body = case_node.value.body
                if isinstance(body, dict) and isinstance(body.get("commitDate"), str):
                    values.append(body["commitDate"])
    return values


def _collect_query_tokens(schema, *, phase) -> list[str]:
    values: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == phase:
            for case_node in event.recorder.cases.values():
                query = case_node.value.query
                if isinstance(query, dict) and isinstance(query.get("token"), str):
                    values.append(query["token"])
    return values


def test_drf_detail_envelope_does_not_add_detail_query_parameter(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/audio": {
                "get": {
                    "parameters": [{"name": "tags", "in": "query", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        }
    )

    @app.route("/api/audio")
    def audio():
        if request.args.get("tags") == "":
            return jsonify({"detail": {"tags": ["This field may not be blank."]}}), 400
        return jsonify([])

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE], max_examples=30)

    query_keys = set()
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.FUZZING:
            for case_node in event.recorder.cases.values():
                query_keys.update(case_node.value.query or {})
    assert "detail" not in query_keys


def test_wrapped_field_error_does_not_add_property_to_closed_body(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/redeem": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"token": {"type": "string", "minLength": 1}},
                                    "required": ["token"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        }
    )

    @app.route("/redeem", methods=["POST"])
    def redeem():
        body = request.get_json(silent=True)
        if isinstance(body, dict) and isinstance(body.get("token"), str) and len(body["token"]) < 3:
            # The field error sits under an envelope key the schema does not declare.
            return jsonify({"error": "validation_failed", "details": {"token": ["This field may not be blank."]}}), 422
        return jsonify({"ok": True})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=30)

    body_keys = set()
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.FUZZING:
            for case_node in event.recorder.cases.values():
                if isinstance(case_node.value.body, dict):
                    body_keys.update(case_node.value.body)
    assert body_keys == {"token"}


def test_stale_example_evicted_after_format_inference(ctx):
    api = ctx.openapi.serve(error_feedback_apps.commit_date_with_example())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    # A stale example that survives feedback shows up only across many draws.
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=100)

    fuzzing_commit_dates = _collect_body_dates(schema, phase=PhaseName.FUZZING)
    assert fuzzing_commit_dates, "No fuzzing body draws collected"
    stale = [v for v in fuzzing_commit_dates if v == "dd-MM-yyyy"]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(fuzzing_commit_dates)} fuzzing draws"


def test_stale_example_evicted_after_format_inference_on_query_param(ctx):
    api = ctx.openapi.serve(error_feedback_apps.token_with_example())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    # A stale example that survives feedback shows up only across many draws.
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=100)

    fuzzing_token_values = _collect_query_tokens(schema, phase=PhaseName.FUZZING)
    assert fuzzing_token_values, "No fuzzing query draws collected"
    stale = [v for v in fuzzing_token_values if v == "NOT_A_UUID"]
    assert not stale, f"Stale `token`: {len(stale)}/{len(fuzzing_token_values)} fuzzing draws"


def test_example_of_rejected_query_parameter_not_sent(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "get": {
                    "parameters": [{"name": "legacy", "in": "query", "schema": {"type": "string"}, "example": "STALE"}],
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        }
    )

    @app.route("/api/items")
    def items():
        if "legacy" in request.args:
            return jsonify({"message": 'parameter name "legacy" is not allowed', "fieldErrors": []}), 400
        return jsonify([])

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=30)

    fuzzing_queries = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.FUZZING:
            fuzzing_queries.extend(case_node.value.query for case_node in event.recorder.cases.values())
    assert fuzzing_queries, "No fuzzing draws collected"
    assert [query for query in fuzzing_queries if query and "legacy" in query] == []


def test_stale_body_example_evicted_in_coverage_after_examples_observations(ctx):
    api = ctx.openapi.serve(error_feedback_apps.commit_date_with_examples())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_commit_dates = _collect_body_dates(schema, phase=PhaseName.COVERAGE)
    assert coverage_commit_dates, "No coverage body draws collected"
    stale = [v for v in coverage_commit_dates if v in error_feedback_apps.STALE_DATES]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(coverage_commit_dates)} coverage draws"


def test_stale_property_example_evicted_in_coverage_after_examples_observations(ctx):
    api = ctx.openapi.serve(error_feedback_apps.commit_date_with_property_example())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_commit_dates = _collect_body_dates(schema, phase=PhaseName.COVERAGE)
    assert coverage_commit_dates, "No coverage body draws collected"
    stale = [v for v in coverage_commit_dates if v == "dd-MM-yyyy"]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(coverage_commit_dates)} coverage draws"


def test_stale_query_example_evicted_in_coverage_after_examples_observations(ctx):
    api = ctx.openapi.serve(error_feedback_apps.token_with_examples())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_token_values = _collect_query_tokens(schema, phase=PhaseName.COVERAGE)
    assert coverage_token_values, "No coverage query draws collected"
    stale = [v for v in coverage_token_values if v in error_feedback_apps.STALE_TOKENS]
    assert not stale, f"Stale `token`: {len(stale)}/{len(coverage_token_values)} coverage draws"


def test_stateful_body_generation_consumes_format_inferred_during_fuzzing(ctx):
    api = ctx.openapi.serve(error_feedback_apps.commit_date_with_link())
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["fuzzing", "stateful"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=50)

    stateful_commit_dates = _collect_body_dates(schema, phase=PhaseName.STATEFUL_TESTING)
    assert stateful_commit_dates, "No stateful POST body draws collected"
    iso_matches = [v for v in stateful_commit_dates if error_feedback_apps.ISO_DATETIME.match(v)]
    assert iso_matches, (
        f"None of {len(stateful_commit_dates)} stateful `commitDate` draws match the inferred date-time format; "
        f"first few: {stateful_commit_dates[:5]}"
    )
