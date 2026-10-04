import threading

import schemathesis
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName, unit
from test.apps.builders import build_schema, make_flask_app_from_schema


def test_responses_reach_the_census_as_a_phase_runs(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    engine = EngineContext(schema=schema, stop_event=threading.Event())

    list(unit.execute(engine, Phase(name=PhaseName.FUZZING, is_enabled=True)))

    summary = engine.behaviors.summary()

    assert summary.alphabet == "status+labels"
    assert summary.operations["GET /api/success"]["distinct"] == 1
    assert summary.operations["GET /api/success"]["responses"] > 0


def test_a_declared_value_the_service_never_returns_is_reported(app_runner):
    # Three states are documented; the service only ever reaches one of them.
    schema = build_schema(
        {
            "/orders": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"enum": ["new", "paid", "archived"]}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    app = make_flask_app_from_schema(schema)

    @app.route("/orders", methods=["GET"])
    def orders():
        from flask import jsonify

        return jsonify({"status": "new"})

    port = app_runner.run_flask_app(app)
    loaded = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    engine = EngineContext(schema=loaded, stop_event=threading.Event())

    list(unit.execute(engine, Phase(name=PhaseName.FUZZING, is_enabled=True)))

    operation = engine.behaviors.summary().operations["GET /orders"]

    assert operation["never_seen"] == {"status": ["archived", "paid"]}


def test_an_operation_that_shows_every_declared_value_reports_nothing(app_runner):
    schema = build_schema(
        {
            "/orders": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"status": {"enum": ["new"]}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    app = make_flask_app_from_schema(schema)

    @app.route("/orders", methods=["GET"])
    def orders():
        from flask import jsonify

        return jsonify({"status": "new"})

    port = app_runner.run_flask_app(app)
    loaded = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    engine = EngineContext(schema=loaded, stop_event=threading.Event())

    list(unit.execute(engine, Phase(name=PhaseName.FUZZING, is_enabled=True)))

    assert "never_seen" not in engine.behaviors.summary().operations["GET /orders"]
