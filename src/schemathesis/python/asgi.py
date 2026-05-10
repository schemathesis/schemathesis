from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette_testclient import TestClient as ASGIClient


def get_client(app: object) -> ASGIClient:
    from starlette_testclient import TestClient as ASGIClient

    return ASGIClient(app)
