from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from schemathesis.core import NotSet
from schemathesis.core.rate_limit import ratelimit
from schemathesis.core.transforms import merge_at
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.generation.overrides import Override
from schemathesis.python import wsgi
from schemathesis.transport import BaseTransport, SerializationContext
from schemathesis.transport.prepare import (
    get_exclude_headers,
    normalize_base_url,
    prepare_body,
    prepare_headers,
    prepare_path,
)
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.serialization import serialize_binary, serialize_json, serialize_xml, serialize_yaml

if TYPE_CHECKING:
    import werkzeug


class WSGITransport(BaseTransport["werkzeug.Client"]):
    def serialize_case(self, case: Case, **kwargs: Any) -> dict[str, Any]:
        headers = kwargs.get("headers")
        params = kwargs.get("params")

        final_headers = prepare_headers(case, headers)

        media_type = case.media_type

        # Set content type for payload
        if media_type and not isinstance(case.body, NotSet):
            final_headers["Content-Type"] = media_type

        extra: dict[str, Any]
        # Handle serialization
        if not isinstance(case.body, NotSet) and media_type is not None:
            serializer = self._get_serializer(media_type)
            context = SerializationContext(case=case)
            extra = serializer(context, prepare_body(case))
        else:
            extra = {}

        data = {
            "method": case.method,
            "path": case.operation.schema.get_full_path(prepare_path(case.path, case.path_parameters)),
            # Convert to regular dict for Werkzeug compatibility
            "headers": dict(final_headers),
            "query_string": case.query,
            **extra,
        }

        if params is not None:
            merge_at(data, "query_string", params)

        return data

    def send(
        self,
        case: Case,
        *,
        session: werkzeug.Client | None = None,
        **kwargs: Any,
    ) -> Response:
        import requests

        headers = kwargs.pop("headers", None)
        params = kwargs.pop("params", None)
        cookies = kwargs.pop("cookies", None)
        application = kwargs.pop("app")

        data = self.serialize_case(case, headers=headers, params=params)
        data.update({key: value for key, value in kwargs.items() if key not in data})

        excluded_headers = get_exclude_headers(case)
        for name in excluded_headers:
            data["headers"].pop(name, None)

        client = session or wsgi.get_client(application)
        cookies = {**(case.cookies or {}), **(cookies or {})}

        config = case.operation.schema.config
        rate_limit = config.rate_limit_for(operation=case.operation)
        with cookie_handler(client, cookies), ratelimit(rate_limit, config.base_url):
            start = time.monotonic()
            response = client.open(**data)
            elapsed = time.monotonic() - start

        requests_kwargs = REQUESTS_TRANSPORT.serialize_case(
            case,
            base_url=normalize_base_url(case.operation.base_url),
            headers=headers,
            params=params,
            cookies=cookies,
        )

        headers = {key: response.headers.getlist(key) for key in response.headers.keys()}

        return Response(
            status_code=response.status_code,
            headers=headers,
            content=response.get_data(),
            request=requests.Request(**requests_kwargs).prepare(),
            elapsed=elapsed,
            verify=False,
            _override=Override(
                query=kwargs.get("params") or {},
                headers=kwargs.get("headers") or {},
                cookies=kwargs.get("cookies") or {},
                path_parameters={},
                body={},
            ),
        )


@contextmanager
def cookie_handler(client: werkzeug.Client, cookies: dict[str, Any] | None) -> Generator[None, None, None]:
    """Set cookies required for a call."""
    if not cookies:
        yield
    else:
        for key, value in cookies.items():
            client.set_cookie(key=key, value=value, domain="localhost")
        yield
        for key in cookies:
            client.delete_cookie(key=key, domain="localhost")


WSGI_TRANSPORT = WSGITransport()


@WSGI_TRANSPORT.serializer("application/json", "text/json")
def json_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return serialize_json(value)


@WSGI_TRANSPORT.serializer(
    "text/yaml", "text/x-yaml", "text/vnd.yaml", "text/yml", "application/yaml", "application/x-yaml"
)
def yaml_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return serialize_yaml(value)


@WSGI_TRANSPORT.serializer("multipart/form-data", "multipart/mixed")
def multipart_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return {"data": value}


@WSGI_TRANSPORT.serializer("application/xml", "text/xml")
def xml_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return serialize_xml(ctx.case, value)


@WSGI_TRANSPORT.serializer("application/x-www-form-urlencoded")
def urlencoded_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return {"data": value}


@WSGI_TRANSPORT.serializer("text/plain")
def text_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        return {"data": value}
    return {"data": str(value)}


@WSGI_TRANSPORT.serializer("application/octet-stream")
def binary_serializer(ctx: SerializationContext, value: Any) -> dict[str, Any]:
    return {"data": serialize_binary(value)}
