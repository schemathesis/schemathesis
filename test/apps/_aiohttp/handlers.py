import asyncio
import cgi
import csv
import io
from typing import Dict

from aiohttp import web


async def expect_content_type(request: web.Request, value: str):
    if request.headers.get("Content-Type", "") != value:
        raise web.HTTPInternalServerError(text=f"Expected {value} payload")
    return await request.read()


async def success(request: web.Request) -> web.Response:
    return web.json_response({"success": True})


async def payload(request: web.Request) -> web.Response:
    body = await request.read()
    if body:
        return web.json_response(body=body)
    return web.json_response({"name": "Nothing!"})


async def invalid_response(request: web.Request) -> web.Response:
    return web.json_response({"random": "key"})


async def custom_format(request: web.Request) -> web.Response:
    return web.json_response({"value": request.query["id"]})


async def teapot(request: web.Request) -> web.Response:
    return web.json_response({"success": True}, status=418)


async def recursive(request: web.Request) -> web.Response:
    return web.json_response({"children": [{"children": [{"children": []}]}]})


async def text(request: web.Request) -> web.Response:
    return web.Response(body="Text response", content_type="text/plain")


async def plain_text_body(request: web.Request) -> web.Response:
    body = await expect_content_type(request, "text/plain")
    return web.Response(body=body, content_type="text/plain")


async def csv_payload(request: web.Request) -> web.Response:
    body = await expect_content_type(request, "text/csv")
    if body:
        reader = csv.DictReader(body.decode().splitlines())
        data = list(reader)
    else:
        data = []
    return web.json_response(data)


async def headers(request: web.Request) -> web.Response:
    values = dict(request.headers)
    return web.json_response(values, headers=values)


async def malformed_json(request: web.Request) -> web.Response:
    return web.Response(body="{malformed}", content_type="application/json")


async def failure(request: web.Request) -> web.Response:
    raise web.HTTPInternalServerError


async def slow(request: web.Request) -> web.Response:
    await asyncio.sleep(0.1)
    return web.json_response({"slow": True})


async def performance(request: web.Request) -> web.Response:
    # Emulate bad performance on certain input type
    # This endpoint is for Schemathesis targeted testing, the failure should be discovered
    decoded = await request.json()
    number = str(decoded).count("0")
    if number > 0:
        await asyncio.sleep(0.01 * number)
    if number > 10:
        raise web.HTTPInternalServerError
    return web.json_response({"slow": True})


async def unsatisfiable(request: web.Request) -> web.Response:
    return web.json_response({"result": "IMPOSSIBLE!"})


async def flaky(request: web.Request) -> web.Response:
    config = request.app["config"]
    if config["should_fail"]:
        config["should_fail"] = False
        raise web.HTTPInternalServerError
    return web.json_response({"result": "flaky!"})


async def multiple_failures(request: web.Request) -> web.Response:
    id_value = int(request.query["id"])
    if id_value == 0:
        raise web.HTTPInternalServerError
    if id_value > 0:
        raise web.HTTPGatewayTimeout
    return web.json_response({"result": "OK"})


def _decode_multipart(content: bytes, content_type: str) -> Dict[str, str]:
    # a simplified version of multipart encoding that satisfies testing purposes
    _, options = cgi.parse_header(content_type)
    options["boundary"] = options["boundary"].encode()
    options["CONTENT-LENGTH"] = len(content)
    return {
        key: value[0].decode() if isinstance(value[0], bytes) else value[0]
        for key, value in cgi.parse_multipart(io.BytesIO(content), options).items()
    }


async def multipart(request: web.Request) -> web.Response:
    if not request.headers.get("Content-Type", "").startswith("multipart/"):
        raise web.HTTPInternalServerError(text="Not a multipart request!")
    # We need to have payload stored in the request, thus can't use `request.multipart` that consumes the reader
    content = await request.read()
    data = _decode_multipart(content, request.headers["Content-Type"])
    return web.json_response(data)


async def upload_file(request: web.Request) -> web.Response:
    if not request.headers.get("Content-Type", "").startswith("multipart/"):
        raise web.HTTPInternalServerError(text="Not a multipart request!")
    content = await request.read()
    expected_lines = [
        b'Content-Disposition: form-data; name="data"; filename="data"\r\n',
        # "note" field is not file and should be encoded without filename
        b'Content-Disposition: form-data; name="note"\r\n',
    ]
    if any(line not in content for line in expected_lines):
        raise web.HTTPInternalServerError(text="Request does not contain expected lines!")
    return web.json_response({"size": request.content_length})


async def form(request: web.Request) -> web.Response:
    if not request.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
        raise web.HTTPInternalServerError(text="Not an urlencoded request!")
    return web.json_response({"size": request.content_length})


async def create_user(request: web.Request) -> web.Response:
    data = await request.json()
    user_id = len(request.app["users"]) + 1
    request.app["users"][user_id] = {**data, "id": user_id}
    request.app["requests_history"][user_id].append("POST")
    return web.json_response({"id": user_id}, status=201)


async def get_user(request: web.Request) -> web.Response:
    user_id = int(request.match_info["user_id"])
    try:
        user = request.app["users"][user_id]
        request.app["requests_history"][user_id].append("GET")
        return web.json_response(user)
    except KeyError:
        return web.json_response({"message": "Not found"}, status=404)


async def update_user(request: web.Request) -> web.Response:
    user_id = int(request.match_info["user_id"])
    try:
        user = request.app["users"][user_id]
        history = request.app["requests_history"][user_id]
        history.append("PATCH")
        if history == ["POST", "GET", "PATCH", "GET", "PATCH"]:
            raise web.HTTPInternalServerError(text="We got a problem!")
        data = await request.json()
        user["username"] = data["username"]
        return web.json_response(user)
    except KeyError:
        return web.json_response({"message": "Not found"}, status=404)


get_payload = payload
path_variable = success
invalid = success
invalid_path_parameter = success
