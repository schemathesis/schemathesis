from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from typing_extensions import override

from schemathesis.core import Body, NotSet, media_types
from schemathesis.core.rate_limit import ratelimit
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import merge_at, to_wire_text
from schemathesis.core.transport import Response, is_event_stream, read_event_stream
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
from schemathesis.transport.requests import (
    REQUESTS_TRANSPORT,
    _merge_query_components,
    prepare_multipart_parts,
    prepare_query,
)
from schemathesis.transport.serialization import Binary, serialize_binary, serialize_json, serialize_xml, serialize_yaml

if TYPE_CHECKING:
    import requests
    import werkzeug


def _is_raw_multipart(media_type: str, payload: object) -> bool:
    """Whether a multipart payload is passed through as-is instead of being encoded into form parts."""
    return media_types.parse(media_type)[0] == "multipart" and isinstance(payload, (str, bytes))


class WSGITransport(BaseTransport["werkzeug.Client"]):
    @override
    def serialize_case(self, case: Case, **kwargs: Any) -> dict[str, Any]:
        headers = kwargs.get("headers")
        params_override = kwargs.get("params")
        base_url = kwargs.get("base_url")

        final_headers = prepare_headers(case, headers)

        media_type = case.media_type

        serializer = None
        if not isinstance(case.body, NotSet) and media_type is not None:
            media_type, serializer = self._resolve_serializer(media_type)

        extra: dict[str, Any]
        # Handle serialization
        if serializer is not None:
            context = SerializationContext(case=case)
            extra = serializer(context, prepare_body(case))
        else:
            extra = {}

        # Set content type for payload. A raw multipart payload is sent verbatim and carries no
        # boundary, so declaring it as multipart would make it unparsable for the application.
        if media_type and not isinstance(case.body, NotSet) and not _is_raw_multipart(media_type, extra.get("data")):
            final_headers.setdefault("Content-Type", media_type)

        data = {
            "method": case.method,
            "path": case.operation.schema.get_full_path(prepare_path(case.path, case.path_parameters)),
            # Convert to regular dict for Werkzeug compatibility
            "headers": dict(final_headers),
            "query_string": prepare_query(case),
            **extra,
        }

        if base_url is not None:
            # Werkzeug reads a path in its `base_url` as the mount point, so it only gets the origin.
            url = urlsplit(case.operation.schema.build_request_url(case, base_url))
            data["path"] = url.path
            data["base_url"] = urlunsplit((url.scheme, url.netloc, "", "", ""))

        if params_override is not None:
            if isinstance(data.get("query_string"), str) or isinstance(params_override, str):
                data["query_string"] = _merge_query_components(data.get("query_string"), params_override)
            else:
                merge_at(data, "query_string", params_override)

        return data

    @override
    def send(
        self,
        case: Case,
        *,
        session: werkzeug.Client | requests.Session | None = None,
        **kwargs: Any,
    ) -> Response:
        import requests

        headers = kwargs.pop("headers", None)
        params = kwargs.pop("params", None)
        cookies = kwargs.pop("cookies", None)
        timeout = kwargs.pop("timeout", None)
        auth = None
        if isinstance(session, requests.Session):
            # A network session still configures the call, but the application is reached in-process.
            headers = {**session.headers, **(headers or {})}
            cookies = {**session.cookies.get_dict(), **(cookies or {})}
            auth = session.auth
            session = None
            # Socket-level settings have no in-process equivalent.
            for name in ("max_redirects", "verify", "cert", "proxies"):
                kwargs.pop(name, None)
        application = kwargs.pop("app")
        base_url = normalize_base_url(kwargs.pop("base_url", None), host=wsgi.HOST)

        data = self.serialize_case(case, headers=headers, params=params, base_url=base_url)
        data.update({key: value for key, value in kwargs.items() if key not in data})

        excluded_headers = get_exclude_headers(case)
        for name in excluded_headers:
            data["headers"].pop(name, None)
        if auth is not None and "Authorization" not in excluded_headers:
            data["auth"] = auth

        client = session or wsgi.get_client(application)
        cookies = {**(case.cookies or {}), **(cookies or {})}

        config = case.operation.schema.config
        rate_limit = config.rate_limit_for(operation=case.operation)

        with (
            _capture_server_exception(application) as captured,
            cookie_handler(client, cookies),
            ratelimit(rate_limit, config.base_url),
        ):
            started_at = Instant()
            response = client.open(**data)
            elapsed = started_at.elapsed

        if captured.exception is not None:
            raise captured.exception

        requests_kwargs = REQUESTS_TRANSPORT.serialize_case(
            case,
            base_url=base_url or normalize_base_url(case.operation.base_url, host=wsgi.HOST),
            headers=headers,
            params=params,
            cookies=cookies,
        )

        headers = {}
        for name, value in response.headers:
            headers.setdefault(name.lower(), []).append(value)

        if is_event_stream(response.content_type):
            # An event stream may never end; stop reading once the request timeout passes in total.
            content, stream_cut_short = read_event_stream(
                response.response,
                timeout or config.request_timeout_for(operation=case.operation),
                config.max_stream_events_for(operation=case.operation),
            )
            response.close()
        else:
            content = response.get_data()
            stream_cut_short = False

        return Response(
            status_code=response.status_code,
            headers=headers,
            content=content,
            request=requests.Request(**requests_kwargs).prepare(),
            elapsed=elapsed,
            verify=False,
            stream_cut_short=stream_cut_short,
            _override=Override(
                query=kwargs.get("params") or {},
                headers=kwargs.get("headers") or {},
                cookies=kwargs.get("cookies") or {},
                path_parameters={},
                body={},
            ),
        )


class _CapturedServerException:
    """Container for a server-side exception captured via Flask signal."""

    __slots__ = ("exception",)

    def __init__(self) -> None:
        self.exception: BaseException | None = None


@contextmanager
def _capture_server_exception(application: object) -> Generator[_CapturedServerException, None, None]:
    """Capture unhandled exceptions from Flask apps."""
    captured = _CapturedServerException()
    try:
        from flask import Flask, got_request_exception
        from werkzeug.exceptions import HTTPException
    except ImportError:
        yield captured
        return

    if not isinstance(application, Flask):
        yield captured
        return

    def _on_exception(sender: Flask, exception: BaseException, **_: object) -> None:
        # Some extensions emit this signal for HTTP errors they already turned into a response
        if isinstance(exception, HTTPException):
            return
        captured.exception = exception

    got_request_exception.connect(_on_exception, application)
    try:
        yield captured
    finally:
        got_request_exception.disconnect(_on_exception, application)


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
def json_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    return serialize_json(value)


@WSGI_TRANSPORT.serializer(
    "text/yaml", "text/x-yaml", "text/vnd.yaml", "text/yml", "application/yaml", "application/x-yaml"
)
def yaml_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    return serialize_yaml(value)


def _to_werkzeug_parts(files: list | None, data: dict[str, Any] | None) -> dict[str, list]:
    """Reshape multipart parts into the field mapping Werkzeug encodes into a request body."""
    from werkzeug.datastructures import FileStorage

    parts: dict[str, list] = {}
    for name, value in (data or {}).items():
        parts.setdefault(name, []).extend(value if isinstance(value, list) else [value])
    for name, part in files or []:
        if isinstance(part, tuple):
            filename, content, *rest = part
            content_type = rest[0] if rest else None
        else:
            # A bare value carries no filename of its own and is named after its field
            filename, content, content_type = name, part, None
        if filename is None and content_type is None and not isinstance(content, (bytes, Binary)):
            parts.setdefault(name, []).append(content)
        else:
            # Only a file-like part keeps its bytes intact and can carry a filename
            storage = FileStorage(BytesIO(serialize_binary(content)), filename, name, content_type)
            parts.setdefault(name, []).append(storage)
    return parts


@WSGI_TRANSPORT.serializer("multipart/form-data", "multipart/mixed")
def multipart_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"data": value}
    files, data = prepare_multipart_parts(ctx, value)
    parts = _to_werkzeug_parts(files, data)
    subtype = media_types.parse(ctx.case.media_type or "multipart/form-data")[1]
    if subtype in ("form-data", "*"):
        return {"data": parts}
    from werkzeug.test import stream_encode_multipart

    # Werkzeug builds a body only for `multipart/form-data`, so other subtypes are encoded here
    stream, _, boundary = stream_encode_multipart(parts)
    return {"data": stream.read(), "content_type": f'multipart/{subtype}; boundary="{boundary}"'}


@WSGI_TRANSPORT.serializer("application/xml", "text/xml")
def xml_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    return serialize_xml(ctx.case, value)


@WSGI_TRANSPORT.serializer("application/x-www-form-urlencoded")
def urlencoded_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    return {"data": value}


@WSGI_TRANSPORT.serializer("text/plain")
def text_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    if isinstance(value, bytes):
        return {"data": value}
    return {"data": to_wire_text(value)}


@WSGI_TRANSPORT.serializer("application/octet-stream")
def binary_serializer(ctx: SerializationContext, value: Body) -> dict[str, Any]:
    return {"data": serialize_binary(value)}
