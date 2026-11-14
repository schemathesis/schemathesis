from __future__ import annotations

from collections.abc import Callable, Iterable


class FlaskAdapter:
    name = "flask"

    def matches(self, app: object) -> bool:
        try:
            from flask import Flask
        except ImportError:  # pragma: no cover
            return False
        return isinstance(app, Flask)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        from flask import Flask

        assert isinstance(app, Flask)
        return list(app.view_functions.values())

    def modules(self, app: object) -> Iterable[str]:
        from flask import Flask

        assert isinstance(app, Flask)
        # Route scanning misses the app module's resolvers and literals when handlers are library views, e.g. GraphQL.
        return [app.import_name]
