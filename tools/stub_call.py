from __future__ import annotations

from unittest.mock import patch

import requests

from schemathesis import Case
from schemathesis.core.transport import Response


def install_stub_call(*, headers: dict[str, list[str]] | None = None, content: bytes = b"{}") -> None:
    """Make every `Case.call` return a fixed 200 response without sending a request."""
    response = Response(
        status_code=200,
        headers={"Content-Type": ["application/json"]} if headers is None else headers,
        content=content,
        request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
        elapsed=0.1,
        verify=False,
    )

    def call(case: Case, *args: object, **kwargs: object) -> Response:
        # The real call freezes metadata before sending, so `case.meta` does not re-hash containers on every access.
        case.freeze_metadata()
        return response

    patch("schemathesis.Case.call", new=call).start()
