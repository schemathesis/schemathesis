import asyncio

from aiohttp import web


async def success(request: web.Request) -> web.Response:
    return web.json_response({"success": True})


async def teapot(request: web.Request) -> web.Response:
    return web.json_response({"success": True}, status=418)


async def text(request: web.Request) -> web.Response:
    return web.Response(body="Text response", content_type="text/plain")


async def failure(request: web.Request) -> web.Response:
    raise web.HTTPInternalServerError


async def slow(request: web.Request) -> web.Response:
    await asyncio.sleep(0.25)
    return web.json_response({"slow": True})


async def unsatisfiable(request: web.Request) -> web.Response:
    return web.json_response({"result": "IMPOSSIBLE!"})


SHOULD_FAIL = True


async def flaky(request: web.Request) -> web.Response:
    global SHOULD_FAIL
    if SHOULD_FAIL:
        SHOULD_FAIL = False
        raise web.HTTPInternalServerError
    SHOULD_FAIL = True
    return web.json_response({"result": "flaky!"})


async def multipart(request: web.Request) -> web.Response:
    if not request.headers["Content-Type"].startswith("multipart/"):
        raise web.HTTPUnsupportedMediaType
    data = {field.name: (await field.read()).decode() async for field in await request.multipart()}
    return web.json_response(data)
