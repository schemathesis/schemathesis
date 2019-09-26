import asyncio
import threading
from test.utils import SIMPLE_PATH
from time import sleep

import pytest
from aiohttp import web

from schemathesis.runner import execute, get_base_url


@pytest.fixture()
def app():
    saved_requests = []

    async def schema(request):
        return web.FileResponse(SIMPLE_PATH)

    async def users(request):
        saved_requests.append(request)
        return web.Response(text="Hello, world")

    app = web.Application()
    app.add_routes([web.get("/swagger.yaml", schema), web.get("/v1/users", users)])
    app["saved_requests"] = saved_requests
    return app


def run_server(app):
    # Set a loop for a new thread (there is no by default for non-main threads)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 8080)
    loop.run_until_complete(site.start())
    loop.run_forever()


@pytest.fixture()
def server(app):
    t = threading.Thread(target=run_server, args=(app,))
    t.daemon = True
    t.start()
    sleep(0.05)  # Wait for the app startup
    yield


@pytest.mark.usefixtures("server")
def test_execute(app):
    execute("http://127.0.0.1:8080/swagger.yaml")
    assert len(app) == 1
    assert app["saved_requests"][0].path == "/v1/users"
    assert app["saved_requests"][0].method == "GET"


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
