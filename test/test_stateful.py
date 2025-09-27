import pytest

import schemathesis
from schemathesis.core.errors import InvalidStateMachine

pytestmark = [pytest.mark.openapi_version("3.0")]


def test_missing_operation(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/users/": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "OK",
                            "links": {
                                "GetUserByUserId": {
                                    "operationId": "unknown",
                                    "parameters": {"path.user_id": "$response.body#/id"},
                                },
                            },
                        }
                    },
                }
            },
            "/users/{user_id}": {
                "get": {"operationId": "getUser", "responses": {"200": {"description": "OK"}}},
            },
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    with pytest.raises(InvalidStateMachine) as exc:
        schema.as_state_machine()
    assert "Operation 'unknown' not found" in str(exc.value)
