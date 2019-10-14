import requests

from schemathesis import Case


def test_path():
    case = Case(method="GET", path="/users/{name}", path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


def test_as_requests_kwargs(server, base_url):
    case = Case(method="GET", path="/api/success")
    data = case.as_requests_kwargs(base_url)
    assert data == {
        "headers": {},
        "json": {},
        "method": "GET",
        "params": {},
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


def test_call(base_url):
    case = Case(method="GET", path="/api/success")
    response = case.call(base_url)
    assert response.status_code == 200
    assert response.json() == {"success": True}
