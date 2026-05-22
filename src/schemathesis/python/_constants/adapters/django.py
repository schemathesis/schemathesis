from __future__ import annotations

from collections.abc import Callable, Iterable


class DjangoAdapter:
    name = "django"

    def matches(self, app: object) -> bool:
        try:
            from django.core.handlers.base import BaseHandler  # type: ignore[import-not-found, unused-ignore]
        except ImportError:
            return False
        return isinstance(app, BaseHandler)

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        try:
            from django.urls import get_resolver  # type: ignore[import-not-found, unused-ignore]
            from django.urls.resolvers import URLPattern, URLResolver  # type: ignore[import-not-found, unused-ignore]
        except ImportError:
            return []

        def walk(resolver: URLResolver) -> Iterable[Callable[..., object]]:
            for item in resolver.url_patterns:
                if isinstance(item, URLResolver):
                    yield from walk(item)
                elif isinstance(item, URLPattern) and callable(item.callback):
                    yield item.callback

        return list(walk(get_resolver()))
