from schemathesis.schemas import APIOperation


def test_formatted_path(swagger_20):
    operation = APIOperation("/users/{name}", "GET", {}, swagger_20, responses=swagger_20._parse_responses({}, ""))
    case = operation.Case(path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"
