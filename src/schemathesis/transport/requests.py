from __future__ import annotations

import binascii
import inspect
import os
from collections.abc import MutableMapping
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from schemathesis.core import NotSet
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.rate_limit import ratelimit
from schemathesis.core.transforms import merge_at
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT, Response
from schemathesis.generation.overrides import Override
from schemathesis.transport import BaseTransport, SerializationContext
from schemathesis.transport.prepare import get_exclude_headers, prepare_body, prepare_headers, prepare_url
from schemathesis.transport.serialization import Binary, serialize_binary, serialize_json, serialize_xml, serialize_yaml

if TYPE_CHECKING:
    import requests

    from schemathesis.generation.case import Case


class RequestsTransport(BaseTransport["requests.Session"]):
    def serialize_case(self, case: Case, **kwargs: Any) -> dict[str, Any]:
        base_url = kwargs.get("base_url")
        headers = kwargs.get("headers")
        params = kwargs.get("params")
        cookies = kwargs.get("cookies")

        final_headers = prepare_headers(case, headers)

        media_type = case.media_type

        # Set content type header if needed
        if media_type and media_type != "multipart/form-data" and not isinstance(case.body, NotSet):
            if "content-type" not in final_headers:
                final_headers["Content-Type"] = media_type

        url = prepare_url(case, base_url)

        # Handle serialization
        if not isinstance(case.body, NotSet) and media_type is not None:
            serializer = self._get_serializer(media_type)
            context = SerializationContext(case=case)
            extra = serializer(context, prepare_body(case))
        else:
            extra = {}

        if case._auth is not None:
            extra["auth"] = case._auth

        # Additional headers from serializer
        additional_headers = extra.pop("headers", None)
        if additional_headers:
            for key, value in additional_headers.items():
                final_headers.setdefault(key, value)

        params = case.query

        # Replace empty dictionaries with empty strings, so the parameters actually present in the query string
        if any(value == {} for value in (params or {}).values()):
            params = dict(params)
            for key, value in params.items():
                if value == {}:
                    params[key] = ""

        data = {
            "method": case.method,
            "url": url,
            "cookies": case.cookies,
            "headers": final_headers,
            "params": params,
            **extra,
        }

        if params is not None:
            merge_at(data, "params", params)
        if cookies is not None:
            merge_at(data, "cookies", cookies)

        excluded_headers = get_exclude_headers(case)
        for name in excluded_headers:
            data["headers"].pop(name, None)

        return data

    def send(self, case: Case, *, session: requests.Session | None = None, **kwargs: Any) -> Response:
        import requests

        config = case.operation.schema.config

        max_redirects = kwargs.pop("max_redirects", None) or config.max_redirects_for(operation=case.operation)
        timeout = config.request_timeout_for(operation=case.operation)
        verify = config.tls_verify_for(operation=case.operation)
        cert = config.request_cert_for(operation=case.operation)
        proxies = config.proxy_for(operation=case.operation)

        if session is not None and session.headers:
            # These headers are explicitly provided via config or CLI args.
            # They have lower priority than ones provided via `kwargs`
            headers = kwargs.setdefault("headers", {}) or {}
            for name, value in session.headers.items():
                headers.setdefault(name, value)
            kwargs["headers"] = headers

        data = self.serialize_case(case, **kwargs)

        if verify is not None:
            data.setdefault("verify", verify)
        if timeout is not None:
            data.setdefault("timeout", timeout)
        if cert is not None:
            data.setdefault("cert", cert)

        kwargs.pop("base_url", None)
        for key, value in kwargs.items():
            if key not in ("headers", "cookies", "params") or key not in data:
                data[key] = value
        data.setdefault("timeout", DEFAULT_RESPONSE_TIMEOUT)
        if proxies is not None:
            data.setdefault("proxies", {"all": proxies})

        current_session_headers: MutableMapping[str, Any] = {}
        current_session_auth = None

        if session is None:
            validate_vanilla_requests_kwargs(data)
            session = requests.Session()
            close_session = True
        else:
            current_session_headers = session.headers
            if isinstance(session.auth, tuple):
                excluded_headers = get_exclude_headers(case)
                if "Authorization" in excluded_headers:
                    current_session_auth = session.auth
                    session.auth = None
            close_session = False
        if max_redirects is not None:
            session.max_redirects = max_redirects
        session.headers = {}

        verify = data.get("verify", True)

        try:
            rate_limit = config.rate_limit_for(operation=case.operation)
            with ratelimit(rate_limit, config.base_url):
                response = session.request(**data)
            return Response.from_requests(
                response,
                verify=verify,
                _override=Override(
                    query=kwargs.get("params") or {},
                    headers=kwargs.get("headers") or {},
                    cookies=kwargs.get("cookies") or {},
                    path_parameters={},
                    body={},
                ),
            )
        finally:
            session.headers = current_session_headers
            if current_session_auth is not None:
                session.auth = current_session_auth
            if close_session:
                session.close()


def validate_vanilla_requests_kwargs(data: dict[str, Any]) -> None:
    """Check arguments for `requests.Session.request`.

    Some arguments can be valid for cases like ASGI integration, but at the same time they won't work for the regular
    `requests` calls. In such cases we need to avoid an obscure error message, that comes from `requests`.
    """
    url = data["url"]
    if not urlparse(url).netloc:
        stack = inspect.stack()
        method_name = "call"
        for frame in stack[1:]:
            if frame.function == "call_and_validate":
                method_name = "call_and_validate"
                break
        raise IncorrectUsage(
            "The `base_url` argument is required when specifying a schema via a file, so Schemathesis knows where to send the data. \n"
            f"Pass `base_url` either to the `schemathesis.openapi.from_*` loader or to the `Case.{method_name}`.\n"
            f"If you use the ASGI integration, please supply your test client "
            f"as the `session` argument to `call`.\nURL: {url}"
        )


REQUESTS_TRANSPORT = RequestsTransport()


@REQUESTS_TRANSPORT.serializer("application/json", "text/json")
def json_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return serialize_json(value)


@REQUESTS_TRANSPORT.serializer(
    "text/yaml", "text/x-yaml", "text/vnd.yaml", "text/yml", "application/yaml", "application/x-yaml"
)
def yaml_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return serialize_yaml(value)


def _should_coerce_to_bytes(item: Any) -> bool:
    """Whether the item should be converted to bytes."""
    # These types are OK in forms, others should be coerced to bytes
    return isinstance(item, Binary) or not isinstance(item, (bytes, str, int))


def _prepare_form_data(data: dict[str, Any]) -> dict[str, Any]:
    """Make the generated data suitable for sending as multipart.

    If the schema is loose, Schemathesis can generate data that can't be sent as multipart. In these cases,
    we convert it to bytes and send it as-is, ignoring any conversion errors.

    NOTE. This behavior might change in the future.
    """
    for name, value in data.items():
        if isinstance(value, list):
            data[name] = [serialize_binary(item) if _should_coerce_to_bytes(item) else item for item in value]
        elif _should_coerce_to_bytes(value):
            data[name] = serialize_binary(value)
    return data


def choose_boundary() -> str:
    """Random boundary name."""
    return binascii.hexlify(os.urandom(16)).decode("ascii")


def _encode_multipart(value: Any, boundary: str) -> bytes:
    """Encode any value as multipart.

    NOTE. It doesn't aim to be 100% correct multipart payload, but rather a way to send data which is not intended to
    be used as multipart, in cases when the API schema dictates so.
    """
    # For such cases we stringify the value and wrap it to a randomly-generated boundary
    body = BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(str(value).encode())
    body.write(f"--{boundary}--\r\n".encode("latin-1"))
    return body.getvalue()


@REQUESTS_TRANSPORT.serializer("multipart/form-data", "multipart/mixed")
def multipart_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        return {"data": value}
    if isinstance(value, dict):
        multipart = _prepare_form_data(value)
        files, data = ctx.case.operation.prepare_multipart(multipart, ctx.case.multipart_content_types)
        return {"files": files, "data": data}
    # Uncommon schema. For example - `{"type": "string"}`
    boundary = choose_boundary()
    raw_data = _encode_multipart(value, boundary)
    content_type = f"multipart/form-data; boundary={boundary}"
    return {"data": raw_data, "headers": {"Content-Type": content_type}}


@REQUESTS_TRANSPORT.serializer("application/xml", "text/xml")
def xml_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return serialize_xml(ctx.case, value)


@REQUESTS_TRANSPORT.serializer("application/x-www-form-urlencoded")
def urlencoded_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return {"data": value}


@REQUESTS_TRANSPORT.serializer("text/plain")
def text_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        return {"data": value}
    return {"data": str(value).encode("utf8")}


@REQUESTS_TRANSPORT.serializer("application/octet-stream")
def binary_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return {"data": serialize_binary(value)}
