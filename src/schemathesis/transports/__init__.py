from __future__ import annotations

import base64
import inspect
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, Any, Generator, Protocol, TypeVar
from urllib.parse import urlparse

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.failures import Failure

from ..constants import DEFAULT_RESPONSE_TIMEOUT, SCHEMATHESIS_TEST_CASE_HEADER
from ..serializers import SerializerContext

if TYPE_CHECKING:
    import requests
    import werkzeug
    from _typeshed.wsgi import WSGIApplication
    from requests.structures import CaseInsensitiveDict
    from starlette_testclient._testclient import ASGI2App, ASGI3App

    from ..models import Case
    from .responses import WSGIResponse


class RequestTimeout(Failure):
    """Request took longer than timeout."""

    timeout: int
    message: str
    title: str = "Response timeout"
    code: str = "request_timeout"

    @property
    def _unique_key(self) -> str:
        return str(self.timeout)


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


def get(app: Any) -> Transport:
    """Get transport to send the data to the application."""
    if app is None:
        return RequestsTransport()
    if iscoroutinefunction(app) or (
        hasattr(app, "__call__") and iscoroutinefunction(app.__call__)  # noqa: B004
    ):
        return ASGITransport(app=app)
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
    ) -> dict[str, Any]:
        raise NotImplementedError

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
    ) -> R:
        raise NotImplementedError


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
        media_type: str | None
        if case.body is not NOT_SET and case.media_type is None:
            media_type = case.operation._get_default_media_type()
        else:
            media_type = case.media_type
        if media_type and media_type != "multipart/form-data" and not isinstance(case.body, NotSet):
            # `requests` will handle multipart form headers with the proper `boundary` value.
            if "content-type" not in final_headers:
                final_headers["Content-Type"] = media_type
        url = case._get_url(base_url)
        serializer = case._get_serializer(media_type)
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

        data = self.serialize_case(case, base_url=base_url, headers=headers, params=params, cookies=cookies)
        data.update(kwargs)
        data.setdefault("timeout", DEFAULT_RESPONSE_TIMEOUT)
        if session is None:
            validate_vanilla_requests_kwargs(data)
            session = requests.Session()
            close_session = True
        else:
            close_session = False
        verify = data.get("verify", True)
        with case.operation.schema.ratelimit():
            response = session.request(**data)  # type: ignore
        response.verify = verify  # type: ignore[attr-defined]
        response._session = session  # type: ignore[attr-defined]
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
        stack = inspect.stack()
        method_name = "call"
        for frame in stack[1:]:
            if frame.function == "call_and_validate":
                method_name = "call_and_validate"
                break
        raise RuntimeError(
            "The `base_url` argument is required when specifying a schema via a file, so Schemathesis knows where to send the data. \n"
            f"Pass `base_url` either to the `schemathesis.from_*` loader or to the `Case.{method_name}`.\n"
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
        media_type: str | None
        if case.body is not NOT_SET and case.media_type is None:
            media_type = case.operation._get_default_media_type()
        else:
            media_type = case.media_type
        if media_type and not isinstance(case.body, NotSet):
            # If we need to send a payload, then the Content-Type header should be set
            final_headers["Content-Type"] = media_type
        extra: dict[str, Any]
        serializer = case._get_serializer(media_type)
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

        application = kwargs.pop("app", self.app) or self.app
        data = self.serialize_case(case, headers=headers, params=params)
        data.update(kwargs)
        client = werkzeug.Client(application, WSGIResponse)
        cookies = {**(case.cookies or {}), **(cookies or {})}
        with cookie_handler(client, cookies), case.operation.schema.ratelimit():
            start = time.monotonic()
            response = client.open(**data)
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


@lru_cache
def get_request_signature() -> inspect.Signature:
    import requests

    return inspect.signature(requests.Request)


@dataclass
class PreparedRequestData:
    method: str
    url: str
    body: str | bytes | None
    headers: dict[str, Any]


def prepare_request_data(kwargs: dict[str, Any]) -> PreparedRequestData:
    """Prepare request data for generating code samples."""
    import requests

    kwargs = {key: value for key, value in kwargs.items() if key in get_request_signature().parameters}
    request = requests.Request(**kwargs).prepare()
    return PreparedRequestData(
        method=str(request.method), url=str(request.url), body=request.body, headers=dict(request.headers)
    )


@lru_cache
def get_excluded_headers() -> CaseInsensitiveDict:
    from requests.structures import CaseInsensitiveDict
    from requests.utils import default_headers

    # These headers are added automatically by Schemathesis or `requests`.
    # Do not show them in code samples to make them more readable

    return CaseInsensitiveDict(
        {
            "Content-Length": None,
            "Transfer-Encoding": None,
            SCHEMATHESIS_TEST_CASE_HEADER: None,
            **default_headers(),
        }
    )
