from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.python import asgi
from schemathesis.transport.prepare import normalize_base_url
from schemathesis.transport.requests import REQUESTS_TRANSPORT, RequestsTransport

if TYPE_CHECKING:
    import requests


class ASGITransport(RequestsTransport):
    @override
    def send(self, case: Case, *, session: requests.Session | None = None, **kwargs: Any) -> Response:
        if kwargs.get("base_url") is None:
            # The same host the schema was fetched with, so a `Host`-validating app sees one host per run.
            kwargs["base_url"] = normalize_base_url(case.operation.base_url, host=asgi.HOST)
        application = kwargs.pop("app", case.operation.app)

        if isinstance(session, asgi.ASGIClient):
            return super().send(case, session=session, **kwargs)

        max_stream_events = case.operation.schema.config.max_stream_events_for(operation=case.operation)
        with asgi.get_client(application, max_stream_events=max_stream_events) as client:
            if session is not None:
                # A network session still configures the call, but the application is reached in-process.
                client.headers.update(session.headers)
                client.auth = session.auth
                client.cookies.update(session.cookies)
            return super().send(case, session=client, **kwargs)


ASGI_TRANSPORT = ASGITransport()
ASGI_TRANSPORT._copy_serializers_from(REQUESTS_TRANSPORT)
