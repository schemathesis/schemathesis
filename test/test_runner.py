from aiohttp import web
import pytest
from schemathesis.runner import execute

from test.utils import SIMPLE_PATH


@pytest.fixture
def app():
    async def schema(request):
        return web.FileResponse(SIMPLE_PATH)

    async def hello(request):
        return web.Response(text="Hello, world")

    app = web.Application()
    app.add_routes([
        web.get('/swagger.json', schema),
        web.get('/get', hello),
    ])

    return app


async def test_execute(swagger_20, aiohttp_server, app):
    server = await aiohttp_server(app, port=8080)
    execute("http://127.0.0.1:8080/swagger.json", swagger_20)
