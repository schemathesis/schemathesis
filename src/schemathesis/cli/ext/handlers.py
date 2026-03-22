from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.handlers.base import EventHandler

CUSTOM_HANDLERS: list[type[EventHandler]] = []


def handler() -> Callable[[type], None]:
    """Register a custom CLI event handler for `st run` and `st fuzz`."""

    def _wrapper(cls: type) -> None:
        CUSTOM_HANDLERS.append(cls)

    return _wrapper
