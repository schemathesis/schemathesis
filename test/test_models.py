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
    kwargs = {"method": "GET", "path": "/api/success", "cookies": {"TOKEN": "secret"}}
    if override:
        case = Case(**kwargs)
        data = case.as_requests_kwargs(base_url)
    else:
        case = Case(base_url=base_url, **kwargs)
        data = case.as_requests_kwargs()
    assert data == {
        "headers": None,
        "json": None,
        "method": "GET",
        "params": None,
        "cookies": {"TOKEN": "secret"},
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.filterwarnings("always")
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
    with pytest.warns(None) as records:
        del response
    assert not records


@pytest.mark.parametrize(
    "case, expected",
    (
        (
            Case(method="GET", path="/api/success", base_url="http://example.com", body={"test": 1}),
            "requests.get('http://example.com/api/success', json={'test': 1})",
        ),
        (
            Case(method="GET", path="/api/success", base_url="http://example.com"),
            "requests.get('http://example.com/api/success')",
        ),
        (
            Case(method="GET", path="/api/success", base_url="http://example.com", query={"a": 1}),
            "requests.get('http://example.com/api/success', params={'a': 1})",
        ),
        (
            Case(method="GET", path="/api/success", query={"a": 1}),
            "requests.get('http://localhost/api/success', params={'a': 1})",
        ),
    ),
)
def test_get_code_to_reproduce(case, expected):
    assert case.get_code_to_reproduce() == expected
