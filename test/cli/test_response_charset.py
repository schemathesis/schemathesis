import json
import uuid

import pytest
from flask import Response


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("charset", ["bogus-xyz", "undefined"], ids=["unknown-charset", "undefined-codec"])
def test_bad_charset_response_does_not_crash(ctx, cli, snapshot_cli, charset):
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return Response(b"boom", content_type=f"text/plain; charset={charset}", status=500)

    assert cli.run_openapi_app(app, "--max-examples=1", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("charset", ["bogus-xyz", "undefined"], ids=["unknown-charset", "undefined-codec"])
def test_bad_charset_on_successful_response_does_not_crash(ctx, cli, snapshot_cli, charset):
    # A 2xx JSON response with a bad charset is recorded for reuse in later tests; it must not abort the run.
    item_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": item_schema}},
                            "links": {
                                "GetItemById": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create_item():
        return Response(
            json.dumps({"id": uuid.uuid4().hex}),
            content_type=f"application/json; charset={charset}",
            status=201,
        )

    @app.route("/items/<item_id>", methods=["GET"])
    def get_item(item_id):
        return Response(json.dumps({}), content_type="application/json")

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=3",
            "--checks=not_a_server_error",
            "--mode=positive",
            config={"phases": {"fuzzing": {"extra-data-sources": {"responses": True}}}},
        )
        == snapshot_cli
    )
