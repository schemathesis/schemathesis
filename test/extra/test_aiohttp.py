import requests

from schemathesis.extra._aiohttp import run_server

from ..apps.openapi import _aiohttp


def test_exact_port():
    app = _aiohttp.create_app(("success", "failure"))
    run_server(app, 8999)
    response = requests.get("http://127.0.0.1:8999/schema.yaml", timeout=1)
    assert response.status_code == 200
