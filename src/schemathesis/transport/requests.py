from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.transforms import merge_at
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT, Response

from ..serializers import SerializerContext

if TYPE_CHECKING:
    import requests

    from ..models import Case


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
            merge_at(data, "params", params)
        if cookies is not None:
            merge_at(data, "cookies", cookies)
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
        if close_session:
            session.close()
        return Response.from_requests(response, verify=verify)


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
            f"Pass `base_url` either to the `schemathesis.openapi.from_*` loader or to the `Case.{method_name}`.\n"
            f"If you use the ASGI integration, please supply your test client "
            f"as the `session` argument to `call`.\nURL: {url}"
        )
