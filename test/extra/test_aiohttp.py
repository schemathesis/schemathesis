import requests

from schemathesis.extra._aiohttp import run_server

from ..apps import _aiohttp

from schemathesis.constants import USER_AGENT


def test_exact_port():
    app = _aiohttp.create_openapi_app(("success", "failure"))
    run_server(app, 8999)
    headers = {"User-Agent": USER_AGENT}
    response = requests.get("http://localhost:8999/schema.yaml", headers=headers)
    assert response.status_code == 200
