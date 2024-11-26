from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette_testclient import TestClient as ASGIClient


def get_client(app: Any) -> ASGIClient:
    from starlette_testclient import TestClient as ASGIClient

    return ASGIClient(app)
