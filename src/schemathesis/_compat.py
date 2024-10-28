from typing import Any, Type

from ._lazy_import import lazy_import

__all__ = [  # noqa: F822
    "MultipleFailures",
]


def _load_multiple_failures() -> Type:
    try:
        return BaseExceptionGroup  # type: ignore
    except NameError:
        from exceptiongroup import BaseExceptionGroup as MultipleFailures  # type: ignore

        return MultipleFailures


_imports = {
    "MultipleFailures": _load_multiple_failures,
}


def __getattr__(name: str) -> Any:
    # Some modules are relatively heavy, hence load them lazily to improve startup time for CLI
    return lazy_import(__name__, name, _imports, globals())
