import schemathesis
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import _iter_coverage_cases

MALFORMED_REGEX = "^[A-Za-z0-9 \\\\-.'À-ÿ]+$"


def test_skip_positive_cases_when_required_body_cannot_be_generated(ctx):
    schema_dict = ctx.openapi.build_schema(
        {
            "/api/orders/{orderId}": {
                "put": {
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9A-Z]{26}$"},
                        },
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "X-Optional",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string", "pattern": MALFORMED_REGEX}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.2",
    )
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/api/orders/{orderId}"]["PUT"]

    cases = list(
        _iter_coverage_cases(
            operation=operation,
            generation_modes=[GenerationMode.POSITIVE],
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=schema.config.generation,
        )
    )

    assert len(cases) == 0, f"Expected 0 cases but got {len(cases)} cases with body={[c.body for c in cases]}"
