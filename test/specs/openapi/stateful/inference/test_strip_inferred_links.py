from schemathesis.engine.observations import LocationHeaderEntry
from schemathesis.specs.openapi.stateful.dependencies import strip_inferred_links
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION


def test_strip_removes_only_dependency_analysis_links(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "OK",
                            "headers": {"Location": {"schema": {"type": "string"}}},
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                            "links": {
                                "AuthorWritten": {
                                    "operationId": "deleteUser",
                                    "parameters": {"user_id": "$response.body#/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/users/{user_id}": {
                "get": {
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                    "operationId": "getUser",
                },
                "delete": {
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "Deleted"}},
                    "operationId": "deleteUser",
                },
            },
        }
    )
    post = schema["/users"]["POST"]
    # dep-analysis runs first via as_state_machine(); location-headers runs after via the
    # inferencer. Reversed order would let the location-headers subset-check filter out the
    # dep-analysis link (same target op + same parameter name set), so the test would have
    # no dep-analysis link to strip.
    schema.as_state_machine()
    entry = LocationHeaderEntry(status_code=201, value="/users/abc")
    schema.analysis.inferencer.inject_links(post.responses, [entry])

    response = post.responses.get("201")
    links_keyword = schema.adapter.links_keyword
    before = dict(response.definition.get(links_keyword, {}))
    assert any(
        isinstance(link, dict) and link.get(SCHEMATHESIS_LINK_EXTENSION, {}).get("source") == "location-headers"
        for link in before.values()
    )
    assert any(
        isinstance(link, dict) and link.get(SCHEMATHESIS_LINK_EXTENSION, {}).get("source") == "dependency-analysis"
        for link in before.values()
    )
    assert "AuthorWritten" in before

    strip_inferred_links(schema)

    after = response.definition.get(links_keyword, {})
    assert "AuthorWritten" in after, "author-declared link must survive"
    sources = {
        link.get(SCHEMATHESIS_LINK_EXTENSION, {}).get("source") for link in after.values() if isinstance(link, dict)
    }
    assert "dependency-analysis" not in sources, "dependency-analysis links must be stripped"
    assert "location-headers" in sources, "Location-Header inferred links must survive"
