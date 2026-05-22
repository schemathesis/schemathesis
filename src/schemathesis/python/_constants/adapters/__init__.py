from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Protocol

from schemathesis.python._constants.adapters.django import DjangoAdapter
from schemathesis.python._constants.adapters.fastapi import FastAPIAdapter
from schemathesis.python._constants.adapters.flask import FlaskAdapter

if TYPE_CHECKING:
    from schemathesis.python._constants.orchestrator import ExtractionError


class FrameworkAdapter(Protocol):
    name: str

    def matches(self, app: object) -> bool: ...
    def handlers(self, app: object) -> Iterable[Callable[..., object]]: ...


def select_adapter(
    app: object,
    *,
    adapters: Iterable[FrameworkAdapter],
    errors: list[ExtractionError] | None = None,
    source: str | None = None,
) -> FrameworkAdapter | None:
    """First adapter whose `matches(app)` returns True; None if none do.

    Adapters whose `matches` raises are skipped (never fatal). When `errors`/`source` are
    provided, the exception is recorded as `adapter_error` so users can diagnose a broken
    third-party adapter instead of seeing a silently empty pool.
    """
    from schemathesis.python._constants.orchestrator import ExtractionError

    for adapter in adapters:
        try:
            if adapter.matches(app):
                return adapter
        except Exception as exc:
            if errors is not None and source is not None:
                errors.append(
                    ExtractionError(
                        source=source,
                        reason="adapter_error",
                        detail=f"{getattr(adapter, 'name', type(adapter).__name__)}.matches: {exc}",
                    )
                )
            continue
    return None


def default_adapters() -> list[FrameworkAdapter]:
    """Built-in adapters in first-match-wins order."""
    return [FastAPIAdapter(), FlaskAdapter(), DjangoAdapter()]
