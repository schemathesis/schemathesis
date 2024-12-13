from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.transforms import merge_at
from schemathesis.core.transport import Response
from schemathesis.python import wsgi
from schemathesis.transport.requests import RequestsTransport

from ..serializers import SerializerContext

if TYPE_CHECKING:
    import werkzeug
    from _typeshed.wsgi import WSGIApplication

    from ..models import Case


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
            merge_at(data, "query_string", params)
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
    ) -> Response:
        import requests

        application = kwargs.pop("app", self.app) or self.app
        data = self.serialize_case(case, headers=headers, params=params)
        data.update(kwargs)
        client = wsgi.get_client(application)
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
        headers = {key: response.headers.getlist(key) for key in response.headers.keys()}
        return Response(
            status_code=response.status_code,
            headers=headers,
            content=response.get_data(),
            request=requests.Request(**requests_kwargs).prepare(),
            elapsed=elapsed,
            verify=False,
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
