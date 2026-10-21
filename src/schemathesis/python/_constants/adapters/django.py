from __future__ import annotations

from collections.abc import Callable, Iterable


class DjangoAdapter:
    name = "django"

    def matches(self, app: object) -> bool:
        try:
            from django.core.handlers.base import BaseHandler
        except ImportError:  # pragma: no cover
            return False
        return isinstance(app, BaseHandler)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        try:
            from django.urls import get_resolver
            from django.urls.resolvers import URLPattern, URLResolver
        except ImportError:  # pragma: no cover
            return []

        def walk(resolver: URLResolver) -> Iterable[Callable[..., object]]:
            for item in resolver.url_patterns:
                if isinstance(item, URLResolver):
                    yield from walk(item)
                elif isinstance(item, URLPattern) and callable(item.callback):
                    yield item.callback

        return list(walk(get_resolver()))

    def modules(self, app: object) -> Iterable[str]:
        return ()
