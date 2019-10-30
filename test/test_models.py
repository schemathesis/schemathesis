import pytest
import requests

from schemathesis import Case


def test_path():
    case = Case(method="GET", path="/users/{name}", path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.parametrize("converter", (lambda x: x, lambda x: x + "/"))
def test_as_requests_kwargs(override, server, base_url, converter):
    base_url = converter(base_url)
    kwargs = {"method": "GET", "path": "/api/success"}
    if override:
        case = Case(**kwargs)
        data = case.as_requests_kwargs(base_url)
    else:
        case = Case(base_url=base_url, **kwargs)
        data = case.as_requests_kwargs()
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


@pytest.mark.parametrize("override", (False, True))
def test_call(override, base_url):
    kwargs = {"method": "GET", "path": "/api/success"}
    if override:
        case = Case(**kwargs)
        response = case.call(base_url)
    else:
        case = Case(base_url=base_url, **kwargs)
        response = case.call()
    assert response.status_code == 200
    assert response.json() == {"success": True}
