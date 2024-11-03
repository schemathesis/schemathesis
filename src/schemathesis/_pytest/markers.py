from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..schemas import BaseSchema

HANDLE_MARKER = "_schemathesis_handle"


def get_schemathesis_handle(func: Callable) -> BaseSchema | None:
    from ..schemas import BaseSchema

    try:
        item = getattr(func, HANDLE_MARKER, None)
        # Comparison is needed to avoid false-positives when mocks are collected by pytest
        if isinstance(item, BaseSchema):
            return item
        return None
    except Exception:
        return None


def set_schemathesis_handle(func: Callable, handle: BaseSchema) -> None:
    setattr(func, HANDLE_MARKER, handle)


def has_schemathesis_handle(func: Callable) -> bool:
    """Check whether the test has a Schemathesis handle."""
    return get_schemathesis_handle(func) is not None
