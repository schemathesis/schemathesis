import requests

from schemathesis.extra._aiohttp import run_server

from ..apps import _aiohttp


def test_exact_port():
    app = _aiohttp.create_openapi_app(("success", "failure"))
    run_server(app, 8999)
    response = requests.get("http://localhost:8999/schema.yaml")
    assert response.status_code == 200
