import pytest

from schemathesis.models import APIOperation, OperationDefinition
from schemathesis.specs.openapi.links import get_container
from schemathesis.specs.openapi.parameters import OpenAPI30Parameter


def test_get_container_invalid_location(swagger_20):
    operation = APIOperation(
        path="/users/{user_id}",
        method="get",
        schema=swagger_20,
        verbose_name="GET /users/{user_id}",
        definition=OperationDefinition(
            raw={},
            resolved={},
            scope="",
        ),
    )
    parameters = [
        OpenAPI30Parameter({"in": "query", "name": "code", "type": "integer"}),
        OpenAPI30Parameter({"in": "query", "name": "user_id", "type": "integer"}),
        OpenAPI30Parameter({"in": "query", "name": "common", "type": "integer"}),
    ]
    for parameter in parameters:
        operation.add_parameter(parameter)
    case = operation.make_case()
    with pytest.raises(ValueError, match="Parameter `unknown` is not defined in API operation `GET /users/{user_id}`"):
        get_container(case, None, "unknown")


def test_custom_link_name(openapi_30):
    # When `name` is used with `add_link`
    operation = openapi_30["/users"]["GET"]
    name = "CUSTOM_NAME"
    openapi_30.add_link(source=operation, target=operation, status_code="200", parameters={}, name=name)
    # Then the resulting link has that name
    links = openapi_30.get_links(openapi_30["/users"]["GET"])
    assert name in links["200"]
