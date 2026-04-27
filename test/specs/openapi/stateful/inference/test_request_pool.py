from __future__ import annotations

import pytest
from flask import jsonify


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_request_pool_captures_path_parameters(cli, app_runner, snapshot_cli, ctx):
    paths = {
        "/products/{productName}": {
            "post": {
                "operationId": "createProduct",
                "parameters": [
                    {
                        "name": "productName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"201": {"description": "Created"}},
            },
            "get": {
                "operationId": "getProduct",
                "parameters": [
                    {
                        "name": "productName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "404": {"description": "Not found"},
                },
            },
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    products: set[str] = set()

    @app.route("/products/<product_name>", methods=["POST"])
    def create_product(product_name):
        products.add(product_name)
        return "", 201

    @app.route("/products/<product_name>", methods=["GET"])
    def get_product(product_name):
        if product_name not in products:
            return "", 404
        # Planted bug: required `name` is null for products that exist
        return jsonify({"name": None}), 200

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )
