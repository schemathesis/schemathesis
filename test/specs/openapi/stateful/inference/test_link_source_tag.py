from schemathesis.engine.observations import LocationHeaderEntry
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION


def test_location_header_inferred_link_carries_source(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "post": {
                    "operationId": "createThing",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/things/{thing_id}": {
                "get": {
                    "operationId": "getThing",
                    "parameters": [
                        {"name": "thing_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    post = schema["/things"]["POST"]
    entry = LocationHeaderEntry(status_code=201, value="/things/abc-123")
    schema.analysis.inferencer.inject_links(post.responses, [entry])
    response = post.responses.get("201")
    links = response.definition.get(schema.adapter.links_keyword) or {}
    inferred = [
        link
        for link in links.values()
        if isinstance(link, dict)
        and SCHEMATHESIS_LINK_EXTENSION in link
        and link[SCHEMATHESIS_LINK_EXTENSION].get("is_inferred")
    ]
    assert inferred, "expected at least one Location-Header inferred link"
    assert all(link[SCHEMATHESIS_LINK_EXTENSION].get("source") == "location-headers" for link in inferred)


def test_dependency_analysis_inferred_link_carries_source(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/users/{userId}/profile": {
                "get": {
                    "parameters": [
                        {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    schema.as_state_machine()
    post = schema["/users"]["POST"]
    response = post.responses.get("201")
    links = response.definition.get(schema.adapter.links_keyword) or {}
    inferred = [
        link
        for link in links.values()
        if isinstance(link, dict)
        and SCHEMATHESIS_LINK_EXTENSION in link
        and link[SCHEMATHESIS_LINK_EXTENSION].get("is_inferred")
    ]
    assert inferred, "expected at least one dependency-analysis inferred link"
    assert all(link[SCHEMATHESIS_LINK_EXTENSION].get("source") == "dependency-analysis" for link in inferred)
