from flask import Flask, abort, jsonify, request

# High-entropy literal no random string strategy will realistically produce on its own.
# Reaching the bug below requires this exact value, so the feature must harvest it from source.
UNLOCK_CODE = "a3f9c1e7b5d24680"
LINKED_CODE = "linked_override_value"

SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "constants-e2e", "version": "1.0.0"},
    "paths": {
        "/unlock": {
            "post": {
                "operationId": "unlock",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"code": {"type": "string"}},
                                "required": ["code"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "links": {
                            "retry": {
                                "operationId": "unlock",
                                "requestBody": {"code": "$response.body#/code"},
                            }
                        },
                    }
                },
            }
        }
    },
}

app = Flask(__name__)


@app.route("/openapi.json")
def openapi_schema():
    return jsonify(SCHEMA)


@app.route("/unlock", methods=["POST"])
def unlock():
    data = request.get_json(silent=True)
    if isinstance(data, dict) and data.get("code") == UNLOCK_CODE:
        abort(500)
    return jsonify({"code": LINKED_CODE})
