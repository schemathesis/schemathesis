from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core.transport import Response
from schemathesis.transport.requests import RequestsTransport

if TYPE_CHECKING:
    import requests
    from starlette_testclient._testclient import ASGI2App, ASGI3App

    from ..models import Case


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
    ) -> Response:
        from starlette_testclient import TestClient as ASGIClient

        if base_url is None:
            base_url = case.get_full_base_url()
        with ASGIClient(self.app) as client:
            return super().send(
                case, session=client, base_url=base_url, headers=headers, params=params, cookies=cookies, **kwargs
            )
