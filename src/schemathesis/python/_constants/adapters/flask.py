from __future__ import annotations

from collections.abc import Callable, Iterable


class FlaskAdapter:
    name = "flask"

    def matches(self, app: object) -> bool:
        try:
            from flask import Flask
        except ImportError:
            return False
        return isinstance(app, Flask)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        from flask import Flask

        assert isinstance(app, Flask)
        return list(app.view_functions.values())
