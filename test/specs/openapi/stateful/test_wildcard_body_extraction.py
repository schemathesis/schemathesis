import random

from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.specs.openapi.expressions import MultiMatch
from schemathesis.specs.openapi.stateful import _resolve_multimatch_in_body
from schemathesis.specs.openapi.stateful.links import OpenApiLink


def test_extract_body_resolves_wildcard_pointer_to_multimatch(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/albums": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "content": {
                                                "type": "array",
                                                "items": {"type": "object", "properties": {"id": {"type": "string"}}},
                                            }
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/photos": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"albumId": {"type": "string"}}}
                            }
                        }
                    },
                    "responses": {"201": {"description": "OK"}},
                    "operationId": "createPhoto",
                }
            },
        }
    )
    link_def = {
        "operationId": "createPhoto",
        "requestBody": {"albumId": "$response.body#/content/*/id"},
        "x-schemathesis": {"is_inferred": True, "source": "dependency-analysis"},
    }
    get_albums = schema["/albums"]["GET"]
    link = OpenApiLink(name="CreatePhoto", status_code="200", definition=link_def, source=get_albums)

    case = get_albums.Case()
    raw = response_factory.requests(
        status_code=200,
        content=b'{"content": [{"id": "album-a1b2c3"}, {"id": "album-d4e5f6"}]}',
    )
    output = StepOutput(response=Response.from_requests(raw, True), case=case)

    extracted = link.extract_body(output)
    assert extracted is not None
    assert isinstance(extracted.value, Ok)
    value = extracted.value.ok()
    assert isinstance(value, dict)
    multi = value["albumId"]
    assert isinstance(multi, MultiMatch)
    assert set(multi.values) == {"album-a1b2c3", "album-d4e5f6"}


def test_multimatch_in_body_resolves_to_single_value():
    body = {"albumId": MultiMatch(["album-a1b2c3", "album-d4e5f6"]), "nested": {"foo": "bar"}}
    rng = random.Random(42)
    resolved = _resolve_multimatch_in_body(body, rng)
    assert resolved["albumId"] in {"album-a1b2c3", "album-d4e5f6"}
    assert resolved["nested"] == {"foo": "bar"}
