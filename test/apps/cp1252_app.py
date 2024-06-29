from flask import Flask, jsonify

app = Flask("cp1252")

VALUE = "\uc445"
SCHEMA = {
    "openapi": "3.0.0",
    "paths": {
        "/value": {
            "get": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "enum": [VALUE],
                            }
                        }
                    },
                },
                "responses": {
                    "default": {"description": "Ok"},
                },
            }
        }
    },
}


@app.route("/openapi.json")
def schema():
    return jsonify(SCHEMA)


@app.route("/value", methods=["GET"])
def value():
    return jsonify({"detail": VALUE}), 500
