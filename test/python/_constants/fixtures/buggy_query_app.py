from flask import Flask, abort, jsonify, request

UNLOCK_CODE = "q7e4b1a7c0f9d326"

SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "constants-query-e2e", "version": "1.0.0"},
    "paths": {
        "/unlock": {
            "get": {
                "parameters": [{"name": "code", "in": "query", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

app = Flask(__name__)


@app.route("/openapi.json")
def openapi_schema():
    return jsonify(SCHEMA)


@app.route("/unlock")
def unlock():
    if request.args.get("code") == UNLOCK_CODE:
        abort(500)
    return "ok", 200
