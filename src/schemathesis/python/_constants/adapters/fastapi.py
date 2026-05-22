from __future__ import annotations

from collections.abc import Callable, Iterable


class FastAPIAdapter:
    name = "fastapi"

    def matches(self, app: object) -> bool:
        try:
            from starlette.applications import Starlette
        except ImportError:
            return False
        return isinstance(app, Starlette)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        from starlette.applications import Starlette
        from starlette.routing import Route

        assert isinstance(app, Starlette)
        return [route.endpoint for route in app.routes if isinstance(route, Route)]
