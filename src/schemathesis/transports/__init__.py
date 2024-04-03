from __future__ import annotations
import base64
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING, Any, cast, Generator, Union
from urllib.parse import urlparse

from requests.utils import CaseInsensitiveDict

from .. import failures
from .._dependency_versions import IS_WERKZEUG_ABOVE_3
from ..constants import DEFAULT_RESPONSE_TIMEOUT
from ..exceptions import get_timeout_error
from ..serializers import SerializerContext
from ..types import Cookies, Headers, RequestCert

if TYPE_CHECKING:
    from ..models import Case
    from _typeshed.wsgi import WSGIApplication
    from .responses import WSGIResponse
    from starlette_testclient._testclient import ASGI2App, ASGI3App
    import requests
    import werkzeug


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


def get_transport(app: Any) -> Transport:
    """Get transport to send the data to the application."""
    from starlette.applications import Starlette

    if app is None:
        return RequestsTransport()
    if isinstance(app, Starlette):
        return StarletteTransport(app=app)
    if app.__class__.__module__.startswith("aiohttp."):
        return RequestsTransport()
    return WerkzeugTransport(app=app)


class Transport(Protocol):
    def filter_kwargs(
        self,
        *,
        session: Any,
        headers: dict[str, str] | None,
        verify: bool,
        proxies: dict[str, str] | None,
        cert: RequestCert | None,
        timeout: float | None,
    ) -> dict[str, Any]: ...
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
        session: requests.Session | None = None,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response: ...


@dataclass(repr=False)
class Request:
    """Request data extracted from `Case`."""

    method: str
    url: str
    body: bytes | None
    headers: CaseInsensitiveDict

    @classmethod
    def from_case(cls, case: Case, session: requests.Session) -> Request:
        """Create a new `Request` instance from `Case`."""
        import requests

        base_url = case.get_full_base_url()
        kwargs = case.as_requests_kwargs(base_url)
        request = requests.Request(**kwargs)
        prepared = session.prepare_request(request)  # type: ignore
        return cls.from_prepared_request(prepared)

    @classmethod
    def from_prepared_request(cls, prepared: requests.PreparedRequest) -> Request:
        """A prepared request version is already stored in `requests.Response`."""
        body = prepared.body

        if isinstance(body, str):
            # can be a string for `application/x-www-form-urlencoded`
            body = body.encode("utf-8")

        # these values have `str` type at this point
        url = cast(str, prepared.url)
        method = cast(str, prepared.method)
        return cls(
            url=url,
            method=method,
            headers=prepared.headers,
            body=body,
        )


@dataclass(repr=False)
class Response:
    status_code: int
    message: str
    headers: CaseInsensitiveDict
    body: bytes | None
    encoding: str | None
    http_version: str
    elapsed: float
    verify: bool
    request: Request

    @classmethod
    def from_requests(cls, response: requests.Response, verify: bool) -> Response:
        """Create a response from requests.Response."""
        raw = response.raw
        headers = raw.headers if raw is not None else {}
        # Similar to http.client:319 (HTTP version detection in stdlib's `http` package)
        version = raw.version if raw is not None else 10
        http_version = "1.0" if version == 10 else "1.1"

        def is_empty(_response: requests.Response) -> bool:
            # Assume the response is empty if:
            #   - no `Content-Length` header
            #   - no chunks when iterating over its content
            return "Content-Length" not in headers and list(_response.iter_content()) == []

        body = None if is_empty(response) else response.content
        return cls(
            status_code=response.status_code,
            message=response.reason,
            body=body,
            encoding=response.encoding,
            headers=CaseInsensitiveDict(headers),
            http_version=http_version,
            elapsed=response.elapsed.total_seconds(),
            verify=verify,
            # todo: store the original too? prepared request may not be available
            request=Request.from_prepared_request(response.request),
        )

    @classmethod
    def from_werkzeug(cls, response: WSGIResponse, elapsed: float) -> Response:
        """Create a response from WSGI response."""
        from .responses import get_reason

        message = get_reason(response.status_code)
        # Note, this call ensures that `response.response` is a sequence, which is needed for comparison
        data = response.get_data()
        body = None if response.response == [] else data
        encoding: str | None
        if body is not None:
            # Werkzeug <3.0 had `charset` attr, newer versions always have UTF-8
            encoding = response.mimetype_params.get("charset", getattr(response, "charset", "utf-8"))
        else:
            encoding = None
        return cls(
            status_code=response.status_code,
            message=message,
            body=body,
            encoding=encoding,
            headers=response.headers,
            http_version="1.1",
            elapsed=elapsed,
            verify=True,
            request=Request.from_prepared_request(response.request),
        )

    def json(self, **kwargs: Any) -> Union[list, dict[str, Any], str, int, float, bool]:
        if self.body is None:
            raise ValueError("No body to decode")
        return json.loads(self.body, **kwargs)


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
    # TODO: Re-check of needed
    url = data["url"]
    if not urlparse(url).netloc:
        raise RuntimeError(
            "The URL should be absolute, so Schemathesis knows where to send the data. \n"
            f"If you use the ASGI integration, please supply your test client "
            f"as the `session` argument to `call`.\nURL: {url}"
        )


class RequestsTransport:
    def filter_kwargs(
        self,
        *,
        session: Any,
        headers: dict[str, str] | None,
        verify: bool,
        proxies: dict[str, str] | None,
        cert: RequestCert | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        return {
            "session": session,
            "headers": headers,
            "timeout": timeout,
            "verify": verify,
            "cert": cert,
            "proxies": proxies,
        }

    def serialize_case(
        self,
        case: Case,
        *,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        final_headers = case.build_headers(headers)
        if case.media_type and case.media_type != "multipart/form-data" and not case.has_non_empty_body:
            # `requests` will handle multipart form headers with the proper `boundary` value.
            if "content-type" not in final_headers:
                final_headers["Content-Type"] = case.media_type
        url = case.get_url(base_url)
        serializer = case.get_serializer()
        if serializer is not None and not case.has_non_empty_body:
            context = SerializerContext(case=case)
            extra = serializer.as_requests(context, case.body)
        else:
            extra = {}
        auth = case.get_requests_auth()
        if auth is not None:
            extra["auth"] = auth
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
    ) -> Response:
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
        if close_session:
            session.close()
        return Response.from_requests(response, verify)


@dataclass
class StarletteTransport(RequestsTransport):
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
    ) -> Response:
        from starlette_testclient import TestClient as ASGIClient

        if base_url is None:
            base_url = case.get_full_base_url()
        application = session or self.app
        with ASGIClient(application) as client:
            return super().send(
                case, session=client, base_url=base_url, headers=headers, params=params, cookies=cookies, **kwargs
            )


@dataclass
class WerkzeugTransport:
    app: WSGIApplication

    def filter_kwargs(
        self,
        *,
        session: Any,
        headers: dict[str, str] | None,
        verify: bool,
        proxies: dict[str, str] | None,
        cert: RequestCert | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        return {"headers": headers}

    def serialize_case(
        self,
        case: Case,
        *,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        final_headers = case.build_headers(headers)
        if case.media_type and not case.has_non_empty_body:
            # If we need to send a payload, then the Content-Type header should be set
            final_headers["Content-Type"] = case.media_type
        extra: dict[str, Any]
        serializer = case.get_serializer()
        if serializer is not None and not case.has_non_empty_body:
            context = SerializerContext(case=case)
            extra = serializer.as_werkzeug(context, case.body)
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
        # This should be `app`?
        session: requests.Session | None = None,
        # Should it be ignored?
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response:
        import werkzeug
        import requests
        from .responses import WSGIResponse

        data = self.serialize_case(case, headers=headers, params=params)
        client = werkzeug.Client(self.app, WSGIResponse)
        # TODO: merge cookies
        with cookie_handler(client, case.cookies), case.operation.schema.ratelimit():
            start = time.monotonic()
            response = client.open(**data, **kwargs)
            elapsed = time.monotonic() - start
        # TODO: Unify `request` structure
        requests_kwargs = RequestsTransport().serialize_case(
            case,
            base_url=case.get_full_base_url(),
            headers=headers,
            params=params,
            cookies=cookies,
        )
        response.request = requests.Request(**requests_kwargs).prepare()
        return Response.from_werkzeug(response, elapsed)


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
