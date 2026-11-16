"""Locating the code of in-process applications built with known web frameworks."""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

__all__ = ["ApplicationCode", "application_code"]


@dataclass(frozen=True, slots=True)
class ApplicationCode:
    """Where the code of an in-process application lives."""

    # Web framework the application is built with: `starlette`, `flask` or `django`.
    framework: str
    # Route handlers, without the views the framework itself provides.
    handlers: tuple[Callable[..., object], ...]
    # Modules defining the handlers, plus the modules the framework names as the application's own.
    modules: tuple[str, ...]
    # Directories of the outermost packages holding those modules, or the module files outside any package.
    roots: tuple[str, ...]


def application_code(app: object) -> ApplicationCode | None:
    """Locate the code of an application built with a known framework; `None` for any other application."""
    for adapter in _ADAPTERS:
        if adapter.matches(app):
            try:
                return _locate(app, adapter)
            except Exception:
                # Listing routes runs application code, e.g. Django imports the URL configuration, which can fail.
                return None
    return None


def application_roots(app: object) -> tuple[str, ...]:
    """Roots of the application's own code; empty when its framework is unknown."""
    code = application_code(app)
    return code.roots if code is not None else ()


def _locate(app: object, adapter: FrameworkAdapter) -> ApplicationCode:
    handlers = tuple(_own_handlers(adapter.handlers(app), adapter.packages))
    modules = _unique([*filter(None, map(_module_name, handlers)), *adapter.modules(app)])
    return ApplicationCode(
        framework=adapter.name, handlers=handlers, modules=modules, roots=_unique(filter(None, map(_root, modules)))
    )


def _own_handlers(
    handlers: Iterable[Callable[..., object]], packages: tuple[str, ...]
) -> Iterator[Callable[..., object]]:
    # Views the framework itself provides, e.g. API docs, are not the application's.
    for handler in handlers:
        name = _module_name(handler)
        if name is None or name.partition(".")[0] not in packages:
            yield handler


def _module_name(handler: object) -> str | None:
    module = inspect.getmodule(handler)
    return module.__name__ if module is not None else None


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _root(module_name: str) -> str | None:
    """Directory of the outermost regular package holding a module, or its file when no package holds it."""
    parts = module_name.split(".")
    for end in range(1, len(parts) + 1):
        module = sys.modules.get(".".join(parts[:end]))
        path = getattr(module, "__file__", None)
        if path is None:
            # A namespace package spans unrelated distributions, so the root lies below it.
            continue
        if end < len(parts) or hasattr(module, "__path__"):
            return os.path.dirname(path)
        return path
    return None


class FrameworkAdapter(Protocol):
    name: str
    # Top-level packages of the framework itself; the views they define are not the application's.
    packages: tuple[str, ...]

    def matches(self, app: object) -> bool:
        """Whether this adapter handles `app`."""
        ...  # pragma: no cover

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        """Route handler callables."""
        ...  # pragma: no cover

    def modules(self, app: object) -> Iterable[str]:
        """Module names of the application's code in addition to the handler modules."""
        ...  # pragma: no cover


class StarletteAdapter:
    name = "starlette"
    packages: tuple[str, ...] = ("starlette", "fastapi")

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


class FlaskAdapter:
    name = "flask"
    packages: tuple[str, ...] = ("flask",)

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


class DjangoAdapter:
    name = "django"
    packages: tuple[str, ...] = ("django",)

    def matches(self, app: object) -> bool:
        try:
            from django.core.handlers.base import BaseHandler
        except ImportError:  # pragma: no cover
            return False
        return isinstance(app, BaseHandler)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        from django.urls import get_resolver
        from django.urls.resolvers import URLPattern, URLResolver

        def walk(resolver: URLResolver) -> Iterable[Callable[..., object]]:
            for item in resolver.url_patterns:
                if isinstance(item, URLResolver):
                    yield from walk(item)
                elif isinstance(item, URLPattern) and callable(item.callback):
                    yield item.callback

        return list(walk(get_resolver()))

    def modules(self, app: object) -> Iterable[str]:
        return ()


# First match wins.
_ADAPTERS: tuple[FrameworkAdapter, ...] = (StarletteAdapter(), FlaskAdapter(), DjangoAdapter())
