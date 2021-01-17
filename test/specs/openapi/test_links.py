import pytest

from schemathesis.models import APIOperation, OperationDefinition
from schemathesis.specs.openapi.links import get_container
from schemathesis.specs.openapi.parameters import OpenAPI30Parameter


def test_get_container_invalid_location():
    operation = APIOperation(
        path="/users/{user_id}",
        method="get",
        schema=None,
        definition=OperationDefinition(
            raw={},
            resolved={},
            scope="",
            parameters=[
                OpenAPI30Parameter({"in": "query", "name": "code", "type": "integer"}),
                OpenAPI30Parameter({"in": "query", "name": "user_id", "type": "integer"}),
                OpenAPI30Parameter({"in": "query", "name": "common", "type": "integer"}),
            ],
        ),
    )
    case = operation.make_case()
    with pytest.raises(ValueError, match="Parameter `unknown` is not defined in API operation `GET /users/{user_id}`"):
        get_container(case, None, "unknown")
