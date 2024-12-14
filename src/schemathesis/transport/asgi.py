from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.core.transport import Response
from schemathesis.python import asgi
from schemathesis.transport.requests import REQUESTS_TRANSPORT, RequestsTransport

if TYPE_CHECKING:
    import requests

    from ..models import Case


class ASGITransport(RequestsTransport):
    def send(self, case: Case, *, session: requests.Session | None = None, **kwargs: Any) -> Response:
        if kwargs.get("base_url") is None:
            kwargs["base_url"] = case.get_full_base_url()
        application = kwargs.pop("app", case.app)

        with asgi.get_client(application) as client:
            return super().send(case, session=client, **kwargs)


ASGI_TRANSPORT = ASGITransport()
ASGI_TRANSPORT._copy_serializers_from(REQUESTS_TRANSPORT)
