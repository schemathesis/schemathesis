import pytest
from flask import jsonify


def test_parameter_dictionary_wins_over_values_captured_from_responses(ctx, cli):
    # `GET /languages/` hands `en` to the resource pool; the binding still decides `code`.
    api = ctx.openapi.apps.languages_with_codes()

    cli.run(
        api.schema_url,
        "--phases=fuzzing",
        "--mode=positive",
        "--max-examples=20",
        config={
            "dictionaries": {"spare": {"values": ["fr"]}},
            "parameters": {"path.code": {"dictionary": "spare"}},
        },
    )

    assert {request.path for request in api.requests if request.method == "DELETE"} == {"/languages/fr"}


ITEMS = {
    "/items/{item_id}": {
        "get": {
            "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "OK"}},
        }
    }
}


def _items_app(ctx):
    app, _ = ctx.openapi.make_flask_app(ITEMS)

    @app.route("/items/<item_id>")
    def get_item(item_id):
        return jsonify({"id": item_id})

    return app


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_dictionary_mismatch_warning_for_false_schema(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "disabled", "in": "query", "schema": False}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )

    @app.route("/items")
    def items():
        return jsonify([])

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
            "--max-examples=5",
            config={
                "dictionaries": {"flags": {"values": ["on", "off"]}},
                "parameters": {"query.disabled": {"dictionary": "flags"}},
            },
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_dictionary_mismatch_warning_with_invalid_parameter_schema_in_another_operation(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            **ITEMS,
            "/broken": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "schema": "string"}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/unresolvable": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Missing"}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/items/<item_id>")
    def get_item(item_id):
        return jsonify({"id": item_id})

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
            "--max-examples=5",
            config={
                "dictionaries": {"ids": {"values": ["a", "b", "c", "d", "e", "f", "g", "h", "i", 1]}},
                "parameters": {"path.item_id": {"dictionary": "ids"}},
            },
        )
        == snapshot_cli
    )


def test_no_dictionary_mismatch_warning_when_most_entries_match(ctx, cli):
    result = cli.run_openapi_app(
        _items_app(ctx),
        "--phases=fuzzing",
        "--mode=positive",
        "--max-examples=5",
        config={
            "dictionaries": {"ids": {"values": ["a", "b", "c", "d", 1, 2, 3, 4, 5, 6]}},
            "parameters": {"path.item_id": {"dictionary": "ids"}},
        },
    )

    assert "Dictionary mismatch" not in result.stdout


def _id_app(ctx, types):
    app, _ = ctx.openapi.make_flask_app(
        {
            f"/{resource}/{{id}}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": type_}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
            for resource, type_ in types.items()
        }
    )

    @app.route("/<resource>/<id>")
    def get_resource(resource, id):
        return jsonify({"id": id})

    return app


def run_with_ids(cli, app):
    return cli.run_openapi_app(
        app,
        "--phases=fuzzing",
        "--mode=positive",
        "--max-examples=5",
        config={
            "dictionaries": {"ids": {"values": [1]}},
            "parameters": {"path.id": {"dictionary": "ids"}},
        },
    )


def test_no_dictionary_mismatch_warning_when_few_operations_reject_entries(ctx, cli):
    app = _id_app(ctx, {"items": "integer", "orders": "integer", "carts": "integer", "threads": "string"})

    assert "Dictionary mismatch" not in run_with_ids(cli, app).stdout


def test_dictionary_mismatch_warning_counts_operations_that_reject_entries(ctx, cli):
    app = _id_app(ctx, {"items": "integer", "threads": "string", "notes": "string"})

    assert "on 2 of 3 operations" in run_with_ids(cli, app).stdout


SHORT_CODES = {"type": "string", "maxLength": 2}


# Two operations share both parameters, so each pair is reported once, not once per operation.
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_type_wide_dictionary_mismatch_reported_once_per_parameter(ctx, cli, snapshot_cli):
    parameters = [
        {"name": "lang", "in": "query", "required": True, "schema": SHORT_CODES},
        {"name": "region", "in": "query", "required": True, "schema": SHORT_CODES},
    ]
    app, _ = ctx.openapi.make_flask_app(
        {
            "/books": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}},
            "/films": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/books")
    def books():
        return jsonify([])

    @app.route("/films")
    def films():
        return jsonify([])

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
            "--max-examples=5",
            config={
                "dictionaries": {"words": {"values": ["english", "french", "de"]}},
                "generation": {"dictionaries": {"string": {"dictionary": "words", "probability": 0.5}}},
            },
        )
        == snapshot_cli
    )


def test_examples_phase_fill_missing_uses_examples_phase_dictionaries(ctx, cli):
    app = _items_app(ctx)

    cli.run_openapi_app(
        app,
        "--phases=examples",
        "--mode=positive",
        config={
            "dictionaries": {"fuzzing-ids": {"values": [111]}, "examples-ids": {"values": [222]}},
            "phases": {
                "examples": {
                    "fill-missing": True,
                    "generation": {"dictionaries": {"integer": {"dictionary": "examples-ids", "probability": 1.0}}},
                },
                "fuzzing": {
                    "generation": {"dictionaries": {"integer": {"dictionary": "fuzzing-ids", "probability": 1.0}}}
                },
            },
        },
    )

    assert {request.path for request in app.config["captured_requests"] if request.path.startswith("/items/")} == {
        "/items/222"
    }
