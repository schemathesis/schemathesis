from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from werkzeug import Client


def get_client(app: Any) -> Client:
    from werkzeug import Client

    return Client(app)
