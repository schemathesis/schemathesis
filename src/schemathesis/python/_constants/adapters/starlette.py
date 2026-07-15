from __future__ import annotations

from collections.abc import Callable, Iterable


class StarletteAdapter:
    name = "starlette"

    def matches(self, app: object) -> bool:
        try:
            from starlette.applications import Starlette
        except ImportError:  # pragma: no cover
            return False
        return isinstance(app, Starlette)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        from starlette.applications import Starlette
        from starlette.routing import Host, Mount, Route, WebSocketRoute

        assert isinstance(app, Starlette)

        def iter_handlers(routes: Iterable[object]) -> Iterable[Callable[..., object]]:
            for route in routes:
                if isinstance(route, (Route, WebSocketRoute)):
                    yield route.endpoint
                elif isinstance(route, (Mount, Host)):
                    yield from iter_handlers(route.routes)

        return iter_handlers(app.routes)

    def modules(self, app: object) -> Iterable[str]:
        return ()
