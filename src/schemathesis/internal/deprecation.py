import warnings
from typing import Any, Callable


def _warn_deprecation(*, kind: str, thing: str, removed_in: str, replacement: str) -> None:
    warnings.warn(
        f"{kind} `{thing}` is deprecated and will be removed in Schemathesis {removed_in}. "
        f"Use {replacement} instead.",
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


def warn_filtration_arguments(name: str) -> None:
    _warn_deprecation(kind="Argument", thing=name, removed_in="4.0", replacement="`include` and `exclude` methods")


def deprecated_function(*, removed_in: str, replacement: str) -> Callable:
    def wrapper(func: Callable) -> Callable:
        def inner(*args: Any, **kwargs: Any) -> Any:
            _warn_deprecation(kind="Function", thing=func.__name__, removed_in=removed_in, replacement=replacement)
            return func(*args, **kwargs)

        return inner

    return wrapper
