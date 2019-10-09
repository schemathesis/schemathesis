import asyncio
import threading
from time import sleep

import pytest
import yaml
from aiohttp import web

from schemathesis import __version__
from schemathesis.runner import execute, get_base_url

from .utils import make_schema


@pytest.fixture()
def app():
    saved_requests = []

    async def schema(request):
        raw = make_schema(paths={"/pets": {"get": {}}, "/zerror": {"get": {}}})
        content = yaml.dump(raw)
        return web.Response(body=content)

    async def users(request):
        saved_requests.append(request)
        if app["config"]["raise_exception"]:
            raise web.HTTPInternalServerError
        return web.Response()

    async def pets(request):
        saved_requests.append(request)
        return web.Response()

    app = web.Application()
    app.add_routes(
        [
            web.get("/swagger.yaml", schema),
            web.get("/v1/users", users),
            web.get("/v1/zerror", users),
            web.get("/v1/pets", pets),
        ]
    )
    app["saved_requests"] = saved_requests
    app["config"] = {"raise_exception": False}
    return app


def run_server(app, port):
    # Set a loop for a new thread (there is no by default for non-main threads)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", port)
    loop.run_until_complete(site.start())
    loop.run_forever()


@pytest.fixture()
def server(app, aiohttp_unused_port):
    port = aiohttp_unused_port()
    t = threading.Thread(target=run_server, args=(app, port))
    t.daemon = True
    t.start()
    sleep(0.05)  # Wait for the app startup
    yield {"port": port}


def assert_request(app, idx, method, path, headers=None):
    request = app["saved_requests"][idx]
    assert request.method == method
    assert request.path == path
    if headers:
        for key, value in headers.items():
            assert request.headers.get(key) == value


def assert_not_request(app, method, path):
    for request in app["saved_requests"]:
        assert not (request.path == path and request.method == method)


def test_execute(server, app):
    headers = {"Authorization": "Bearer 123"}
    execute(f"http://127.0.0.1:{server['port']}/swagger.yaml", api_options=dict(headers=headers))
    assert len(app["saved_requests"]) == 3
    assert_request(app, 0, "GET", "/v1/pets", headers)
    assert_request(app, 1, "GET", "/v1/users", headers)


def test_execute_base_url(server, app):
    base_uri = f"http://127.0.0.1:{server['port']}"
    schema_uri = f"{base_uri}/swagger.yaml"

    execute(schema_uri, api_options=dict(base_url=f"{base_uri}/404"))
    assert len(app["saved_requests"]) == 0

    execute(schema_uri, api_options=dict(base_url=base_uri))
    assert len(app["saved_requests"]) == 3


def test_execute_stats(server, app):
    app["config"]["raise_exception"] = True
    stats = execute(f"http://127.0.0.1:{server['port']}/swagger.yaml")
    assert "not_a_server_error" in stats.data
    assert dict(stats.data["not_a_server_error"]) == {"total": 5, "ok": 1, "error": 4}


def test_auth(server, app):
    execute(f"http://127.0.0.1:{server['port']}/swagger.yaml", api_options=dict(auth=("test", "test")))
    assert len(app["saved_requests"]) == 3
    headers = {"Authorization": "Basic dGVzdDp0ZXN0"}
    assert_request(app, 0, "GET", "/v1/pets", headers)
    assert_request(app, 1, "GET", "/v1/users", headers)


def test_execute_filter_endpoint(server, app):
    execute(f"http://127.0.0.1:{server['port']}/swagger.yaml", loader_options=dict(endpoint=["pets"]))
    assert len(app["saved_requests"]) == 1
    assert_request(app, 0, "GET", "/v1/pets")
    assert_not_request(app, "GET", "/v1/users")


def test_execute_filter_method(server, app):
    execute(f"http://127.0.0.1:{server['port']}/swagger.yaml", loader_options=dict(method=["POST"]))
    assert len(app["saved_requests"]) == 0


def test_server_error(server, app):
    app["config"]["raise_exception"] = True
    execute(f"http://127.0.0.1:{server['port']}/swagger.yaml")
    assert len(app["saved_requests"]) == 5
    assert_request(app, 0, "GET", "/v1/pets")
    assert_request(app, 1, "GET", "/v1/users")
    assert_request(app, 2, "GET", "/v1/users")
    assert_request(app, 3, "GET", "/v1/zerror")
    assert_request(app, 4, "GET", "/v1/zerror")


def test_user_agent(server, app):
    execute(f"http://127.0.0.1:{server['port']}/swagger.yaml")
    headers = {"User-Agent": f"schemathesis/{__version__}"}
    assert len(app["saved_requests"]) == 2
    assert_request(app, 0, "GET", "/v1/pets", headers)
    assert_request(app, 1, "GET", "/v1/users", headers)


@pytest.mark.parametrize(
    "url, base_url",
    (
        ("http://127.0.0.1:8080/swagger.json", "http://127.0.0.1:8080"),
        ("https://example.com/get", "https://example.com"),
        ("https://example.com", "https://example.com"),
    ),
)
def test_get_base_url(url, base_url):
    assert get_base_url(url) == base_url
