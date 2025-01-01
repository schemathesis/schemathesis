from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jsonschema import RefResolutionError

try:
    BaseExceptionGroup = BaseExceptionGroup  # type: ignore
except NameError:
    from exceptiongroup import BaseExceptionGroup  # type: ignore


def __getattr__(name: str) -> type[RefResolutionError] | type[BaseExceptionGroup]:
    if name == "RefResolutionError":
        # Import it just once to keep just a single warning
        from jsonschema import RefResolutionError

        return RefResolutionError
    if name == "BaseExceptionGroup":
        return BaseExceptionGroup
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BaseExceptionGroup", "RefResolutionError"]
