from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from schemathesis.python._constants.adapters.django import DjangoAdapter
from schemathesis.python._constants.adapters.flask import FlaskAdapter
from schemathesis.python._constants.adapters.starlette import StarletteAdapter


class FrameworkAdapter(Protocol):
    name: str

    def matches(self, app: object) -> bool:
        """Whether this adapter handles `app`."""
        ...  # pragma: no cover

    def handlers(self, app: object) -> Iterable[Callable[..., object]]:
        """Route handler callables to scan for constants."""
        ...  # pragma: no cover

    def modules(self, app: object) -> Iterable[str]:
        """Module names to scan in addition to the handler modules."""
        ...  # pragma: no cover


def select_adapter(app: object, *, adapters: Iterable[FrameworkAdapter]) -> FrameworkAdapter | None:
    """First adapter whose `matches(app)` returns True; None if none do."""
    for adapter in adapters:
        if adapter.matches(app):
            return adapter
    return None


def default_adapters() -> list[FrameworkAdapter]:
    """Built-in adapters in first-match-wins order."""
    return [StarletteAdapter(), FlaskAdapter(), DjangoAdapter()]
