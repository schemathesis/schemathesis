from flask import Flask, abort, jsonify

MAGIC_ID = -987654

SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "constants-path-e2e", "version": "1.0.0"},
    "paths": {
        "/item/{item_id}": {
            "get": {
                "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

app = Flask(__name__)


@app.route("/openapi.json")
def openapi_schema():
    return jsonify(SCHEMA)


@app.route("/item/<int(signed=True):item_id>")
def item(item_id):
    if item_id == MAGIC_ID:
        abort(500)
    return "ok", 200
