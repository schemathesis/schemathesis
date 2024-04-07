from __future__ import annotations

import base64
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Generator, Protocol, TypeVar, cast
from urllib.parse import urlparse

from .. import failures
from .._dependency_versions import IS_WERKZEUG_ABOVE_3
from ..constants import DEFAULT_RESPONSE_TIMEOUT
from ..exceptions import get_timeout_error
from ..serializers import SerializerContext
from ..types import Cookies, NotSet

if TYPE_CHECKING:
    import requests
    import werkzeug
    from _typeshed.wsgi import WSGIApplication
    from starlette_testclient._testclient import ASGI2App, ASGI3App

    from ..models import Case
    from .responses import WSGIResponse


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


def get(app: Any) -> Transport:
    """Get transport to send the data to the application."""
    from starlette.applications import Starlette

    if app is None:
        return RequestsTransport()
    if isinstance(app, Starlette):
        return ASGITransport(app=app)
    if app.__class__.__module__.startswith("aiohttp."):
        return RequestsTransport()
    return WSGITransport(app=app)


S = TypeVar("S", contravariant=True)
R = TypeVar("R", covariant=True)


class Transport(Protocol[S, R]):
    def serialize_case(
        self,
        case: Case,
        *,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def send(
        self,
        case: Case,
        *,
        session: S | None = None,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> R: ...


class RequestsTransport:
    def serialize_case(
        self,
        case: Case,
        *,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        final_headers = case._get_headers(headers)
        if case.media_type and case.media_type != "multipart/form-data" and not isinstance(case.body, NotSet):
            # `requests` will handle multipart form headers with the proper `boundary` value.
            if "content-type" not in final_headers:
                final_headers["Content-Type"] = case.media_type
        url = case._get_url(base_url)
        serializer = case._get_serializer()
        if serializer is not None and not isinstance(case.body, NotSet):
            context = SerializerContext(case=case)
            extra = serializer.as_requests(context, case._get_body())
        else:
            extra = {}
        if case._auth is not None:
            extra["auth"] = case._auth
        additional_headers = extra.pop("headers", None)
        if additional_headers:
            # Additional headers, needed for the serializer
            for key, value in additional_headers.items():
                final_headers.setdefault(key, value)
        data = {
            "method": case.method,
            "url": url,
            "cookies": case.cookies,
            "headers": final_headers,
            "params": case.query,
            **extra,
        }
        if params is not None:
            _merge_dict_to(data, "params", params)
        if cookies is not None:
            _merge_dict_to(data, "cookies", cookies)
        return data

    def send(
        self,
        case: Case,
        *,
        session: requests.Session | None = None,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        import requests
        from urllib3.exceptions import ReadTimeoutError

        data = self.serialize_case(case, base_url=base_url, headers=headers, params=params, cookies=cookies)
        data.update(kwargs)
        data.setdefault("timeout", DEFAULT_RESPONSE_TIMEOUT / 1000)
        if session is None:
            validate_vanilla_requests_kwargs(data)
            session = requests.Session()
            close_session = True
        else:
            close_session = False
        verify = data.get("verify", True)
        try:
            with case.operation.schema.ratelimit():
                response = session.request(**data)  # type: ignore
        except (requests.Timeout, requests.ConnectionError) as exc:
            if isinstance(exc, requests.ConnectionError):
                if not isinstance(exc.args[0], ReadTimeoutError):
                    raise
                req = requests.Request(
                    method=data["method"].upper(),
                    url=data["url"],
                    headers=data["headers"],
                    files=data.get("files"),
                    data=data.get("data") or {},
                    json=data.get("json"),
                    params=data.get("params") or {},
                    auth=data.get("auth"),
                    cookies=data["cookies"],
                    hooks=data.get("hooks"),
                )
                request = session.prepare_request(req)
            else:
                request = cast(requests.PreparedRequest, exc.request)
            timeout = 1000 * data["timeout"]  # It is defined and not empty, since the exception happened
            code_message = case._get_code_message(case.operation.schema.code_sample_style, request, verify=verify)
            message = f"The server failed to respond within the specified limit of {timeout:.2f}ms"
            raise get_timeout_error(timeout)(
                f"\n\n1. {failures.RequestTimeout.title}\n\n{message}\n\n{code_message}",
                context=failures.RequestTimeout(message=message, timeout=timeout),
            ) from None
        response.verify = verify  # type: ignore[attr-defined]
        if close_session:
            session.close()
        return response


def _merge_dict_to(data: dict[str, Any], data_key: str, new: dict[str, Any]) -> None:
    original = data[data_key] or {}
    for key, value in new.items():
        original[key] = value
    data[data_key] = original


def validate_vanilla_requests_kwargs(data: dict[str, Any]) -> None:
    """Check arguments for `requests.Session.request`.

    Some arguments can be valid for cases like ASGI integration, but at the same time they won't work for the regular
    `requests` calls. In such cases we need to avoid an obscure error message, that comes from `requests`.
    """
    url = data["url"]
    if not urlparse(url).netloc:
        raise RuntimeError(
            "The URL should be absolute, so Schemathesis knows where to send the data. \n"
            f"If you use the ASGI integration, please supply your test client "
            f"as the `session` argument to `call`.\nURL: {url}"
        )


@dataclass
class ASGITransport(RequestsTransport):
    app: ASGI2App | ASGI3App

    def send(
        self,
        case: Case,
        *,
        session: requests.Session | None = None,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        from starlette_testclient import TestClient as ASGIClient

        if base_url is None:
            base_url = case.get_full_base_url()
        with ASGIClient(self.app) as client:
            return super().send(
                case, session=client, base_url=base_url, headers=headers, params=params, cookies=cookies, **kwargs
            )


@dataclass
class WSGITransport:
    app: WSGIApplication

    def serialize_case(
        self,
        case: Case,
        *,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        final_headers = case._get_headers(headers)
        if case.media_type and not isinstance(case.body, NotSet):
            # If we need to send a payload, then the Content-Type header should be set
            final_headers["Content-Type"] = case.media_type
        extra: dict[str, Any]
        serializer = case._get_serializer()
        if serializer is not None and not isinstance(case.body, NotSet):
            context = SerializerContext(case=case)
            extra = serializer.as_werkzeug(context, case._get_body())
        else:
            extra = {}
        data = {
            "method": case.method,
            "path": case.operation.schema.get_full_path(case.formatted_path),
            # Convert to a regular dictionary, as we use `CaseInsensitiveDict` which is not supported by Werkzeug
            "headers": dict(final_headers),
            "query_string": case.query,
            **extra,
        }
        if params is not None:
            _merge_dict_to(data, "query_string", params)
        return data

    def send(
        self,
        case: Case,
        *,
        session: Any = None,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> WSGIResponse:
        import requests
        import werkzeug

        from .responses import WSGIResponse

        data = self.serialize_case(case, headers=headers, params=params)
        application = kwargs.pop("app", self.app) or self.app
        client = werkzeug.Client(application, WSGIResponse)
        cookies = {**(case.cookies or {}), **(cookies or {})}
        with cookie_handler(client, cookies), case.operation.schema.ratelimit():
            start = time.monotonic()
            response = client.open(**data, **kwargs)
            elapsed = time.monotonic() - start
        requests_kwargs = RequestsTransport().serialize_case(
            case,
            base_url=case.get_full_base_url(),
            headers=headers,
            params=params,
            cookies=cookies,
        )
        response.request = requests.Request(**requests_kwargs).prepare()
        response.elapsed = timedelta(seconds=elapsed)
        return response


@contextmanager
def cookie_handler(client: werkzeug.Client, cookies: Cookies | None) -> Generator[None, None, None]:
    """Set cookies required for a call."""
    if not cookies:
        yield
    else:
        for key, value in cookies.items():
            if IS_WERKZEUG_ABOVE_3:
                client.set_cookie(key=key, value=value, domain="localhost")
            else:
                client.set_cookie("localhost", key=key, value=value)
        yield
        for key in cookies:
            if IS_WERKZEUG_ABOVE_3:
                client.delete_cookie(key=key, domain="localhost")
            else:
                client.delete_cookie("localhost", key=key)
