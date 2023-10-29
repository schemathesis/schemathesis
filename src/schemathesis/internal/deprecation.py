import warnings
from typing import Callable, Any


def _warn_deprecation(*, kind: str, thing: str, removed_in: str, replacement: str) -> None:
    warnings.warn(
        f"{kind} `{thing}` is deprecated and will be removed in Schemathesis {removed_in}. "
        f"Use `{replacement}` instead.",
        DeprecationWarning,
        stacklevel=1,
    )


def deprecated_property(*, removed_in: str, replacement: str) -> Callable:
    def wrapper(prop: Callable) -> Callable:
        @property  # type: ignore
        def inner(self: Any) -> Any:
            _warn_deprecation(kind="Property", thing=prop.__name__, removed_in=removed_in, replacement=replacement)
            return prop(self)

        return inner

    return wrapper


def deprecated_function(*, removed_in: str, replacement: str) -> Callable:
    def wrapper(func: Callable) -> Callable:
        def inner(*args: Any, **kwargs: Any) -> Any:
            _warn_deprecation(kind="Function", thing=func.__name__, removed_in=removed_in, replacement=replacement)
            return func(*args, **kwargs)

        return inner

    return wrapper
