from typing import Any, Type, Callable

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata  # type: ignore


__all__ = [  # noqa: F822
    "JSONMixin",
    "InferType",
    "MultipleFailures",
    "get_signature",
    "metadata",
    "_install_hypothesis_jsonschema_compatibility_shim",
]


def _load_json_mixin() -> Type:
    from ._dependency_versions import IS_WERKZEUG_BELOW_2_1

    if IS_WERKZEUG_BELOW_2_1:
        from werkzeug.wrappers.json import JSONMixin
    else:

        class JSONMixin:  # type: ignore
            pass

    return JSONMixin


def _load_infer_type() -> Type:
    try:
        from hypothesis.utils.conventions import InferType

        return InferType
    except ImportError:
        return type(...)


def _load_multiple_failures() -> Type:
    from ._dependency_versions import IS_HYPOTHESIS_ABOVE_6_54

    try:
        return BaseExceptionGroup  # type: ignore
    except NameError:
        if IS_HYPOTHESIS_ABOVE_6_54:
            from exceptiongroup import BaseExceptionGroup as MultipleFailures  # type: ignore
        else:
            from hypothesis.errors import MultipleFailures  # type: ignore

        return MultipleFailures


def _load_get_signature() -> Callable:
    from ._dependency_versions import IS_HYPOTHESIS_ABOVE_6_49

    if IS_HYPOTHESIS_ABOVE_6_49:
        from hypothesis.internal.reflection import get_signature
    else:
        from inspect import getfullargspec as get_signature

    return get_signature


_imports = {
    "JSONMixin": _load_json_mixin,
    "InferType": _load_infer_type,
    "MultipleFailures": _load_multiple_failures,
    "get_signature": _load_get_signature,
}


def __getattr__(name: str) -> Any:
    # Some modules are relatively heavy, hence load them lazily to improve startup time for CLI
    value = globals().get(name)
    if value is not None:
        return value
    loader = _imports.get(name)
    if loader is not None:
        value = loader()
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
