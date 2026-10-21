from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from _typeshed.wsgi import WSGIApplication
    from werkzeug import Client

# `Host` header the test client sends, unless the request overrides it.
HOST = "localhost"


def get_client(app: object) -> Client:
    from werkzeug import Client

    return Client(cast("WSGIApplication", app))
