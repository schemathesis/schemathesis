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
def test_dictionary_mismatch_warning(ctx, cli, snapshot_cli):
    assert (
        cli.run_openapi_app(
            _items_app(ctx),
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
