from __future__ import annotations

import re
from typing import Any

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

_TOPIC_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def planted_bug() -> OpenAPIApp:
    # 400 envelopes use Confluent's `{error_code, message}` shape with the topic-name
    # rule so the parser reads the ASCII pattern straight off the message.
    paths = {
        "/v3/clusters/{cluster_id}/topics": {
            "post": {
                "parameters": [
                    {"name": "cluster_id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "topic_name": {"type": "string"},
                                    "partitions_count": {"type": "integer"},
                                    "replication_factor": {"type": "integer"},
                                },
                                "required": ["topic_name", "partitions_count", "replication_factor"],
                            }
                        }
                    },
                },
                "responses": {
                    "400": {"description": "Bad Request"},
                    "500": {"description": "Server Error"},
                },
            }
        }
    }
    spec = build_schema(paths)
    app = make_flask_app_from_schema(spec)

    @app.route("/v3/clusters/<cluster_id>/topics", methods=["POST"])
    def create_topic(cluster_id: str) -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return (
                jsonify({"error_code": 42206, "message": "Payload error. Null input provided. Data is required."}),
                400,
            )
        topic_name = body.get("topic_name")
        if not isinstance(topic_name, str) or not _TOPIC_NAME_RE.match(topic_name):
            display = topic_name if isinstance(topic_name, str) else ""
            return (
                jsonify(
                    {
                        "error_code": 40002,
                        "message": (
                            f"Topic name is invalid: '{display}' contains one or more "
                            "characters other than ASCII alphanumerics, '.', '_' and '-'"
                        ),
                    }
                ),
                400,
            )
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
