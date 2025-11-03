from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jsonschema import RefResolutionError, RefResolver

try:
    BaseExceptionGroup = BaseExceptionGroup
except NameError:
    from exceptiongroup import BaseExceptionGroup


def __getattr__(name: str) -> type[RefResolutionError] | type[RefResolver] | type[BaseExceptionGroup]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if name == "RefResolutionError":
            # `jsonschema` is pinned, this warning is not useful for the end user
            from jsonschema import RefResolutionError

            return RefResolutionError
        if name == "RefResolver":
            from jsonschema import RefResolver

            return RefResolver
        if name == "BaseExceptionGroup":
            return BaseExceptionGroup
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BaseExceptionGroup", "RefResolutionError"]
