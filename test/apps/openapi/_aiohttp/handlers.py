from __future__ import annotations

import asyncio
import csv
import json
from uuid import uuid4

import jsonschema
from aiohttp import web

from schemathesis.constants import BOM_MARK
from schemathesis.internal.output import MAX_PAYLOAD_SIZE
from schemathesis.transports.content_types import parse_content_type

try:
    from ..schema import PAYLOAD_VALIDATOR
except (ImportError, ValueError):
    from utils import PAYLOAD_VALIDATOR


async def expect_content_type(request: web.Request, value: str):
    content_type = request.headers.get("Content-Type", "")
    main, sub = parse_content_type(content_type)
    if f"{main}/{sub}" != value:
        raise web.HTTPInternalServerError(text=f"Expected {value} payload")
    return await request.read()


async def success(request: web.Request) -> web.Response:
    if request.app["config"]["prefix_with_bom"]:
        return web.Response(body=(BOM_MARK + '{"success": true}').encode(), content_type="application/json")
    return web.json_response({"success": True})


async def conformance(request: web.Request) -> web.Response:
    # The schema expects `value` to be "foo", but it is different every time
    return web.json_response({"value": uuid4().hex})


async def basic(request: web.Request) -> web.Response:
    if "Authorization" in request.headers and request.headers["Authorization"] == "Basic dGVzdDp0ZXN0":
        return web.json_response({"secret": 42})
    raise web.HTTPUnauthorized(text='{"detail": "Unauthorized"}', content_type="application/json")


async def empty(request: web.Request) -> web.Response:
    return web.Response(body=None, status=204)


async def empty_string(request: web.Request) -> web.Response:
    return web.Response(body="")


async def binary(request: web.Request) -> web.Response:
    return web.Response(
        body=b"\xa7\xf5=\x18H\xc7\xff'\xf0\xeep\x06M-RX", content_type="application/octet-stream", status=500
    )


async def long(request: web.Request) -> web.Response:
    return web.Response(body=json.dumps(["A"] * MAX_PAYLOAD_SIZE), content_type="application/json", status=500)


async def payload(request: web.Request) -> web.Response:
    body = await request.read()
    if body:
        data = await request.json()
        try:
            PAYLOAD_VALIDATOR.validate(data)
        except jsonschema.ValidationError as exc:
            raise web.HTTPBadRequest(text=str(exc))  # noqa: B904
        return web.json_response(body=body)
    return web.json_response({"name": "Nothing!"})


async def invalid_response(request: web.Request) -> web.Response:
    return web.json_response({"random": "key"})


async def custom_format(request: web.Request) -> web.Response:
    if "id" not in request.query:
        raise web.HTTPBadRequest(text='{"detail": "Missing `id`"}')
    if not request.query["id"].isdigit():
        raise web.HTTPBadRequest(text='{"detail": "Invalid `id`"}')
    value = request.query["id"]
    return web.json_response({"value": value})


async def teapot(request: web.Request) -> web.Response:
    return web.json_response({"success": True}, status=418)


async def recursive(request: web.Request) -> web.Response:
    return web.json_response({"children": [{"children": [{"children": []}]}]})


async def text(request: web.Request) -> web.Response:
    return web.Response(body="Text response", content_type="text/plain")


async def cp866(request: web.Request) -> web.Response:
    return web.Response(body="Тест".encode("cp866"), content_type="text/plain", charset="cp866")


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


async def ignored_auth(request: web.Request) -> web.Response:
    return web.json_response({"has_auth": "Authorization" in request.headers})


async def malformed_json(request: web.Request) -> web.Response:
    return web.Response(body="{malformed}", content_type="application/json")


async def failure(request: web.Request) -> web.Response:
    raise web.HTTPInternalServerError


async def slow(request: web.Request) -> web.Response:
    await asyncio.sleep(0.1)
    return web.json_response({"success": True})


async def performance(request: web.Request) -> web.Response:
    # Emulate bad performance on certain input type
    # This API operation is for Schemathesis targeted testing, the failure should be discovered
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
    try:
        id_value = int(request.query["id"])
    except KeyError:
        raise web.HTTPBadRequest(text='{"detail": "Missing `id`"}')  # noqa: B904
    except ValueError:
        raise web.HTTPBadRequest(text='{"detail": "Invalid `id`"}')  # noqa: B904
    if id_value == 0:
        raise web.HTTPInternalServerError
    if id_value > 0:
        raise web.HTTPGatewayTimeout
    return web.json_response({"result": "OK"})


async def multipart(request: web.Request) -> web.Response:
    if not request.headers.get("Content-Type", "").startswith("multipart/"):
        raise web.HTTPBadRequest(text="Not a multipart request!")
    raw_payload = await request.read()
    multipart_reader = await request.multipart()
    multipart_reader._content._buffer.append(raw_payload)
    data = {}
    while True:
        part = await multipart_reader.next()
        if part is None:
            break
        data[part.name] = (await part.read()).decode("utf-8")
    return web.json_response(data)


SUCCESS_RESPONSE = {"read": "success!"}


async def read_only(request: web.Request) -> web.Response:
    return web.json_response(SUCCESS_RESPONSE)


async def write_only(request: web.Request) -> web.Response:
    data = await request.json()
    if len(data) == 1 and isinstance(data["write"], int):
        return web.json_response(SUCCESS_RESPONSE)
    raise web.HTTPInternalServerError


async def upload_file(request: web.Request) -> web.Response:
    if not request.headers.get("Content-Type", "").startswith("multipart/"):
        raise web.HTTPBadRequest(text="Not a multipart request!")
    content = await request.read()
    expected_lines = [
        b'Content-Disposition: form-data; name="data"; filename="data"\r\n',
        # "note" field is not file and should be encoded without filename
        b'Content-Disposition: form-data; name="note"\r\n',
    ]
    if any(line not in content for line in expected_lines):
        raise web.HTTPBadRequest(text="Request does not contain expected lines!")
    return web.json_response({"size": request.content_length})


def is_properly_encoded(data: bytes, charset: str) -> bool:
    try:
        data.decode(charset)
        return True
    except UnicodeDecodeError:
        return False


async def form(request: web.Request) -> web.Response:
    if not request.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
        raise web.HTTPInternalServerError(text="Not an urlencoded request!")
    raw = await request.read()
    if not is_properly_encoded(raw, request.charset or "utf8"):
        raise web.HTTPBadRequest(text='{"detail": "Invalid payload"}')
    data = await request.post()
    for field in ("first_name", "last_name"):
        if field not in data:
            raise web.HTTPBadRequest(text=f'{{"detail": "Missing `{field}`"}}')
        if not isinstance(data[field], str):
            raise web.HTTPBadRequest(text=f'{{"detail": "Invalid `{field}`"}}')
    return web.json_response({"size": request.content_length})


async def create_user(request: web.Request) -> web.Response:
    data = await request.json()
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text='{"detail": "Invalid payload"}')
    for field in ("first_name", "last_name"):
        if field not in data:
            raise web.HTTPBadRequest(text=f'{{"detail": "Missing `{field}`"}}')
        if not isinstance(data[field], str):
            raise web.HTTPBadRequest(text=f'{{"detail": "Invalid `{field}`"}}')
    user_id = str(uuid4())
    request.app["users"][user_id] = {**data, "id": user_id}
    return web.json_response({"id": user_id}, status=201)


def get_user_id(request: web.Request) -> str:
    try:
        return request.match_info["user_id"]
    except KeyError:
        raise web.HTTPBadRequest(text='{"detail": "Missing `user_id`"}')  # noqa: B904


async def get_user(request: web.Request) -> web.Response:
    user_id = get_user_id(request)
    try:
        user = request.app["users"][user_id]
        # The full name is done specifically via concatenation to trigger a bug when the last name is `None`
        full_name = user["first_name"] + " " + user["last_name"]
        return web.json_response({"id": user["id"], "full_name": full_name})
    except KeyError:
        return web.json_response({"message": "Not found"}, status=404)


async def update_user(request: web.Request) -> web.Response:
    user_id = get_user_id(request)
    try:
        user = request.app["users"][user_id]
        data = await request.json()
        for field in ("first_name", "last_name"):
            if field not in data:
                raise web.HTTPBadRequest(text=f'{{"detail": "Missing `{field}`"}}')
            # Here we don't check the input value type to emulate a bug in another operation
            user[field] = data[field]
        return web.json_response(user)
    except KeyError:
        return web.json_response({"message": "Not found"}, status=404)


get_payload = payload
path_variable = success
reserved = success
invalid = success
invalid_path_parameter = success
missing_path_parameter = success
